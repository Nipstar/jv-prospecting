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

This same enrichment step also runs the PSC/officer ownership check
(companies_house_client.get_psc — the pre-Phase-1 ad-spend model's
`is_group_owned` concept, still present in companies_house_client.py, now
reused for the chain-exclusion rule; see prospector/chain_signals.py) using
the *same* matched company_number, rather than a second name search.

Chain/franchise/corporate exclusion (standing rule)
-----------------------------------------------------
After Companies House enrichment, every discovered business is run through
prospector.chain_signals.detect_chain() — known chain/franchise brand
name/domain match (primary signal; catches cases Companies House
name-search misses, e.g. "Mildmay Veterinary Hospital" was a CVS Group
sibling practice whose website is on CVS's own cvsvets.com domain but whose
CH name-search false-positive-matched an unrelated London hospice charity
with zero PSC records), `is_group_owned` from the PSC check above
(secondary signal), and a multi-location check (same domain/company number
already seen on another discovered business — extends the existing
place_id/phone/domain dedupe below rather than duplicating it). Matches set
`is_chain=1` + a human-readable `chain_reason`; businesses are NOT dropped
from the pipeline (Andy wants to inspect what got excluded, not lose the
data) — `prospector targets list`/`export` filter them out of the default
view instead (see targets.py), with `--include-chains` to see them anyway.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from prospector import chain_signals, companies_house_client, places_client
from prospector.db import (
    create_run,
    find_business_by_phone_or_domain,
    find_business_by_place_id,
    insert_discovered_business,
    mark_chain_by_company_number,
    mark_chain_by_domain,
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
    established_flag, is_group_owned, director_name} — all None/0/False if
    no confident (non-dissolved) match is found. Fails open (never
    raises) — a Companies House hiccup shouldn't block discovery.

    is_group_owned/director_name reuse the pre-Phase-1 ad-spend model's PSC
    ownership-check logic (companies_house_client.get_psc/get_officers,
    originally wired via check_ownership() for a second name-search — here
    reusing the *same* matched company_number from the incorporation-date
    lookup above instead of searching twice).
    """
    result = {
        "companies_house_number": None,
        "incorporation_date": None,
        "company_status": None,
        "established_flag": 0,
        "is_group_owned": False,
        "director_name": None,
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

    is_group_owned = False
    try:
        pscs = companies_house_client.get_psc(company_number)
        is_group_owned = any((p.get("kind") or "").startswith("corporate-entity") for p in pscs)
    except ApiError:
        pass

    director_name = None
    try:
        for officer in companies_house_client.get_officers(company_number):
            if officer.get("resigned_on"):
                continue
            role = (officer.get("officer_role") or "").lower()
            if role in ("director", "llp-member") and officer.get("name"):
                director_name = officer["name"]
                break
    except ApiError:
        pass

    result.update({
        "companies_house_number": company_number,
        "incorporation_date": incorporation_date,
        "company_status": status,
        "established_flag": established,
        "is_group_owned": is_group_owned,
        "director_name": director_name,
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
    chain_flagged: int = 0
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
                "is_group_owned": False,
                "director_name": None,
            })

        is_chain, chain_reason = chain_signals.detect_chain(conn, biz)
        biz["is_chain"] = int(is_chain)
        biz["chain_reason"] = chain_reason

        business_id = insert_discovered_business(conn, run_id, biz)
        result.inserted += 1
        result.inserted_ids.append(business_id)
        if is_chain:
            result.chain_flagged += 1
            # Retroactively flag any earlier-discovered sibling business
            # that shares this domain/company number too — see module
            # docstring ("multi-location" signal).
            mark_chain_by_domain(conn, biz.get("domain"), chain_reason, exclude_id=business_id)
            mark_chain_by_company_number(conn, biz.get("companies_house_number"), chain_reason, exclude_id=business_id)

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
