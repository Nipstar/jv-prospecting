"""Discovery module — Prospector v2 Phase 2.

Vertical x location Google Places text search ("{vertical} in {location}"),
dedupe, Companies House enrichment, DB write. Reuses/extends
prospector/places_client.py's discover_businesses() (the low-level Google
Places API (New) Text Search HTTP client, already built in a prior session
and still shared with the legacy `prospector run` pipeline) rather than
rebuilding request/pagination/field-mask handling from scratch.

Companies House enrichment
---------------------------
For each discovered business, we best-effort match it to a Companies House
company by name (companies_house_client.search_company), then fetch the
full profile (companies_house_client.get_company_profile — new in this
phase; the existing client only had search/PSC/officer lookups for the
ownership-filter use case, not incorporation date/status). If the matched
company's status is "dissolved" (or a dissolution-adjacent status —
liquidation, receivership, administration — same idea: not a live, callable
business), we treat it as *no confident match* rather than writing stale
CH data against a live Google Places listing: company_number,
incorporation_date, company_status are all left NULL, exactly as if no CH
match had been found. This is "skip dissolved" per Andy's spec, interpreted
as skip-the-enrichment rather than skip-the-business, since the business
itself is still operationally listed on Google (a dissolved company match
could just as easily be a name-search false-positive against a formerly
same-named different company).

established_flag = 1 if incorporation_date is 3+ years before today —
Andy's "more to lose" heuristic for established firms.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from prospector import companies_house_client, places_client
from prospector.db import (
    create_run,
    find_business_by_phone_or_domain,
    find_business_by_place_id,
    insert_discovered_business,
)
from prospector.http import ApiError
from prospector.verticals import resolve_search_term

# Statuses that mean "not a live, callable business" — a match against one
# of these is treated as no-match (see module docstring).
_DISSOLVED_LIKE_STATUSES = {"dissolved", "liquidation", "receivership", "administration", "converted-closed"}

_ESTABLISHED_YEARS = 3

# Loose UK postcode matcher, applied against the end of a Places formatted
# address (Places doesn't return postcode as a separate field). Good enough
# for prospecting purposes; not a full validating postcode regex.
_POSTCODE_RE = re.compile(r"([A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2})\s*,?\s*(United Kingdom|UK)?\s*$")


def _extract_postcode(address: str | None) -> str | None:
    if not address:
        return None
    m = _POSTCODE_RE.search(address.strip())
    return m.group(1).upper().strip() if m else None


def _years_between(d: date, today: date) -> float:
    return (today - d).days / 365.25


def enrich_companies_house(business_name: str) -> dict:
    """Best-effort Companies House enrichment for one business name.

    Returns {company_number, incorporation_date, company_status,
    established_flag} — all None/0 if no confident (non-dissolved) match is
    found. Fails open (never raises) — a Companies House hiccup shouldn't
    block discovery.
    """
    result = {
        "companies_house_number": None,
        "incorporation_date": None,
        "company_status": None,
        "established_flag": 0,
    }
    try:
        match = companies_house_client.search_company(business_name)
    except ApiError:
        return result
    if not match:
        return result

    company_number = match.get("company_number")
    if not company_number:
        return result

    try:
        profile = companies_house_client.get_company_profile(company_number)
    except ApiError:
        profile = None
    if not profile:
        return result

    status = (profile.get("company_status") or "").lower()
    if status in _DISSOLVED_LIKE_STATUSES:
        # Skip dissolved — treat as no confident match (see module docstring).
        return result

    incorporation_date = profile.get("date_of_creation")
    established = 0
    if incorporation_date:
        try:
            d = datetime.strptime(incorporation_date, "%Y-%m-%d").date()
            established = 1 if _years_between(d, date.today()) >= _ESTABLISHED_YEARS else 0
        except ValueError:
            pass

    result.update({
        "companies_house_number": company_number,
        "incorporation_date": incorporation_date,
        "company_status": status,
        "established_flag": established,
    })
    return result


@dataclass
class DiscoverResult:
    run_id: int
    vertical: str
    location: str
    found: int = 0
    deduped_skipped: int = 0
    inserted: int = 0
    inserted_ids: list[int] = field(default_factory=list)


def discover_run(conn, vertical: str, location: str, max_results: int = 20, do_ch: bool = True) -> DiscoverResult:
    """Discover businesses for one vertical/location pair, dedupe against
    what's already in the DB (place_id first, then normalised phone/domain
    fallback — see db.find_business_by_place_id /
    find_business_by_phone_or_domain), optionally enrich via Companies
    House, and write survivors to `businesses`.
    """
    vertical_label, search_term = resolve_search_term(vertical)
    run_id = create_run(conn, location, [vertical_label], notes="discovery/places.py (Phase 2)")
    result = DiscoverResult(run_id=run_id, vertical=vertical_label, location=location)

    discovered = places_client.discover_businesses(search_term, location, "county-wide", max_results)
    result.found = len(discovered)

    for biz in discovered:
        place_id = biz.get("google_place_id")
        if place_id and find_business_by_place_id(conn, place_id):
            result.deduped_skipped += 1
            continue
        # Fallback dedupe on phone/domain regardless of place_id presence,
        # in case the same firm was previously discovered under a
        # different place_id (e.g. Google merged/split listings between
        # runs) — required dedupe fallback per the standing project rules.
        if find_business_by_phone_or_domain(conn, biz.get("phone"), biz.get("domain")):
            result.deduped_skipped += 1
            continue

        biz["vertical"] = vertical_label
        biz["town"] = location
        biz["postcode"] = _extract_postcode(biz.get("address"))

        if do_ch:
            biz.update(enrich_companies_house(biz["name"]))
        else:
            biz.update({
                "companies_house_number": None,
                "incorporation_date": None,
                "company_status": None,
                "established_flag": 0,
            })

        business_id = insert_discovered_business(conn, run_id, biz)
        result.inserted += 1
        result.inserted_ids.append(business_id)

    return result


def import_csv(conn, csv_path: Path, max_results: int = 20, do_ch: bool = True) -> list[DiscoverResult]:
    """Bulk discovery from a CSV of (vertical, location) pairs — columns
    `vertical` and `location` (case-insensitive header match). An optional
    `max_results` column overrides the default per-row.
    """
    results: list[DiscoverResult] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldmap = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}
        if "vertical" not in fieldmap or "location" not in fieldmap:
            raise ValueError("CSV must have 'vertical' and 'location' columns")
        for row in reader:
            vertical = (row.get(fieldmap["vertical"]) or "").strip()
            location = (row.get(fieldmap["location"]) or "").strip()
            if not vertical or not location:
                continue
            row_max = max_results
            if "max_results" in fieldmap:
                raw = (row.get(fieldmap["max_results"]) or "").strip()
                if raw.isdigit():
                    row_max = int(raw)
            results.append(discover_run(conn, vertical, location, max_results=row_max, do_ch=do_ch))
    return results
