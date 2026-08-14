"""Organic-search cross-check/validation module.

SerpAPI organic search (prospector/discovery/organic.py) genuinely
surfaces net-new independent businesses Places/Yell/Checkatrade miss, but
its records are structurally weak — no phone/address/rating from the SERP
itself, and the "business name" is a best-effort guess at the organic
result's page title, which is sometimes a real business name and
sometimes a generic listicle/blog title picked up by SerpAPI as the
result title (see organic.py's module docstring and the README "Known
limitations" section). This module answers the follow-up question: of the
businesses organic search finds, how many are corroborated by an
independent source, and how many are genuinely unvalidated?

Two cross-checks, both reusing existing, already-working clients rather
than scraping directly (per the standing "prefer APIs, escalate to
Playwright/scraping only where there's no API path" philosophy already
established in enrichers/site.py):

1. **GBP cross-check** (this module, `crosscheck_organic`): a targeted
   Google Places Text Search for "{business name} {location}" (
   places_client.find_place_by_name — new, single-result lookup, distinct
   from places_client.discover_businesses's paginated discovery sweep).
   If a real GBP listing turns up (the original discovery pass's generic
   "{vertical} in {location}" query simply didn't surface it — different
   phrasing, or it ranked outside the page-size/page-count ceiling), we
   backfill phone/postcode/rating/review_count/google_place_id from it and
   mark `gbp_crosscheck_status='validated_gbp'` — strong evidence the
   organic result is a real, findable local business. If no match is
   found even with this much more targeted query, that's `no_gbp_found`
   — itself a signal worth keeping (see "no_gbp_found disambiguation"
   below), not a failure to be silently discarded.

2. **Companies House cross-check**: already happens generically for every
   discovered business regardless of source — discovery/places.py's
   discover_run() calls enrich_companies_house(biz["name"]) unconditionally
   inside the per-business loop, not gated on source_name, so organic-
   sourced businesses already get the same CH name-search as Places/Yell/
   Checkatrade-sourced ones (verified by reading discover_run — no fix
   needed here, this module just *reads* the result). A live (non-
   dissolved — enrich_companies_house already treats a dissolved match as
   no-match, see discovery/places.py) companies_house_number on the row is
   therefore itself validation and is folded into `organic_validated`
   below without a second CH lookup.

`no_gbp_found` disambiguation
------------------------------
"No discoverable GBP listing at all" is ambiguous on its own — it could
mean:
  (a) weak_gbp — a real local business with a genuinely poor/absent
      Google Business Profile (exactly prospector's target pain-point:
      hard for their own customers to find them on Google either), or
  (b) not really a business — the organic result is a content/listicle/
      aggregator page that got scraped as if it were a business name
      (organic.py's `_clean_title` is best-effort, not perfect).

We use the one extra signal already cheaply available to lean one way or
the other: does the organic result's own homepage independently read like
a real business's site (a phone number and/or address/contact signal
somewhere on the page) vs. read like a listicle (the *name* itself
matching a listicle pattern — "top N", "best N", "N best", "companies
for/in" — the same failure mode organic.py's own docstring calls out with
the "Local Air Conditioning Companies in London for AC..." example). This
reuses enrichers/site.py's existing fetch-escalation chain
(_fetch_with_escalation — httpx, then Playwright, then Apify) if the
business hasn't already had a `site fetch` pass; if it has (site_checked_at
is set), we reuse that already-fetched signal instead of fetching again.
This is a lightweight heuristic, not a certainty — recorded as a
free-text `gbp_crosscheck_note`, not a hard pass/fail, so Andy can read
the reasoning rather than trust an opaque flag.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from prospector import places_client
from prospector.db import update_business_fields
from prospector.discovery.places import _extract_postcode
from prospector.enrichers.site import _fetch_with_escalation
from prospector.http import ApiError
from prospector.scoring_config import PHONE_PATTERN

# Same class of listicle/aggregator title pattern organic.py's own
# docstring flags as a known failure mode ("Local Air Conditioning
# Companies in London for AC..." landing as a "business name"). Not
# exhaustive — a heuristic, tune as Andy spots more in real runs.
_LISTICLE_NAME_PATTERN = re.compile(
    r"\b(top \d+|\d+\s*best|best \d+|companies (in|for)|for ac\b|guide to|"
    r"compare(d)?|reviews? of|vs\.?\b)\b",
    re.IGNORECASE,
)

_DISSOLVED_LIKE_STATUSES = {"dissolved", "liquidation", "receivership", "administration", "converted-closed"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _looks_like_listicle_name(name: str | None) -> bool:
    return bool(name) and bool(_LISTICLE_NAME_PATTERN.search(name))


def _site_independently_looks_real(biz: dict) -> tuple[bool | None, str]:
    """Best-effort: does this business's own homepage independently read
    like a real business site? Returns (True/False/None, reason). None
    means "couldn't check" (no website, or fetch failed/unreachable) —
    distinct from False ("fetched fine, but doesn't look like a business
    page"). Reuses an already-run `site fetch` result if present (no
    re-fetch); otherwise does a one-off homepage fetch via the same
    fetch-escalation chain enrichers/site.py uses."""
    if biz.get("site_checked_at"):
        # A prior `site fetch` pass already fetched this site — reuse its
        # phone_dependent signal (True there means a phone number and/or a
        # contact form was found on the page) rather than fetching again.
        if biz.get("phone_dependent") is not None:
            looks_real = bool(biz.get("phone_dependent")) or biz.get("phone") is not None
            return looks_real, "reused existing `site fetch` result (phone_dependent/contact signal)"
        return None, "site was previously checked but left no usable signal"

    website = biz.get("website")
    if not website:
        return None, "no website on record — nothing to independently check"

    url = website if website.startswith("http") else f"https://{website}"
    try:
        with httpx.Client() as client:
            result = _fetch_with_escalation(client, url)
    except Exception as exc:  # pragma: no cover - defensive, matches site.py's own broad-catch philosophy
        return None, f"homepage fetch raised unexpectedly ({exc}) — treated as unreachable"

    if result is None:
        return None, "homepage unreachable (all three fetch layers failed) — couldn't independently verify"

    has_phone = bool(re.search(PHONE_PATTERN, result.html))
    has_contact_hint = any(kw in result.html.lower() for kw in ("contact us", "get in touch", "our address", "call us"))
    looks_real = has_phone or has_contact_hint
    reason = "homepage has a phone number/contact signal" if looks_real else "homepage fetched fine but no phone/contact signal found"
    return looks_real, reason


@dataclass
class CrosscheckResult:
    business_id: int
    name: str
    gbp_status: str  # "validated_gbp" | "no_gbp_found" | "error"
    note: str
    organic_validated: bool
    backfilled_fields: dict


def _crosscheck_one(biz: dict) -> CrosscheckResult:
    name = biz["name"]
    location = biz.get("town") or biz.get("postcode") or ""

    ch_number = biz.get("companies_house_number")
    ch_status = (biz.get("company_status") or "").lower()
    ch_live_match = bool(ch_number) and ch_status not in _DISSOLVED_LIKE_STATUSES

    try:
        match = places_client.find_place_by_name(name, location) if location else None
    except ApiError as exc:
        note = f"GBP lookup failed ({exc}) — not counted as validated or invalidated, retry later"
        return CrosscheckResult(
            business_id=biz["id"], name=name, gbp_status="error", note=note,
            organic_validated=ch_live_match, backfilled_fields={},
        )

    if match:
        backfill: dict = {}
        if not biz.get("phone") and match.get("phone"):
            backfill["phone"] = match["phone"]
        if not biz.get("postcode"):
            postcode = _extract_postcode(match.get("address"))
            if postcode:
                backfill["postcode"] = postcode
        if biz.get("rating") is None and match.get("rating") is not None:
            backfill["rating"] = match["rating"]
        if biz.get("review_count") is None and match.get("review_count") is not None:
            backfill["review_count"] = match["review_count"]
        if not biz.get("google_place_id") and match.get("google_place_id"):
            backfill["google_place_id"] = match["google_place_id"]
        note = f"GBP match found: {match.get('name')!r} — backfilled {', '.join(backfill) or '(nothing new; fields already populated)'}"
        return CrosscheckResult(
            business_id=biz["id"], name=name, gbp_status="validated_gbp", note=note,
            organic_validated=True, backfilled_fields=backfill,
        )

    # No GBP match — disambiguate via the listicle-name heuristic and,
    # failing that, an independent look at the homepage.
    if _looks_like_listicle_name(name):
        note = "no_gbp_found; business name matches a listicle/aggregator-title pattern — likely not a genuine business record, not just a weak-GBP one"
    else:
        looks_real, reason = _site_independently_looks_real(biz)
        if looks_real is True:
            note = f"no_gbp_found; {reason} — likely a real business with a weak/absent Google Business Profile (a genuine weak_gbp signal, prospector's target pain-point)"
        elif looks_real is False:
            note = f"no_gbp_found; {reason} — inconclusive, lean toward not-a-business but not certain"
        else:
            note = f"no_gbp_found; {reason}"

    return CrosscheckResult(
        business_id=biz["id"], name=name, gbp_status="no_gbp_found", note=note,
        organic_validated=ch_live_match, backfilled_fields={},
    )


def crosscheck_organic(
    conn, run_id: int | None = None, business_id: int | None = None, refresh: bool = False
) -> list[CrosscheckResult]:
    """Cross-check organic-sourced businesses (discovery_source LIKE
    '%organic%') against Google Business Profile (targeted name+location
    Places lookup) and Companies House (reads the enrichment already
    written during discovery — see module docstring), writing
    gbp_crosscheck_status/gbp_crosscheck_at/gbp_crosscheck_note/
    organic_validated back to each row. Skips businesses already checked
    unless refresh=True. Fails soft per-business (a Places API hiccup on
    one business doesn't block the batch — see _crosscheck_one)."""
    query = "SELECT * FROM businesses WHERE discovery_source LIKE '%organic%'"
    params: list = []
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    if business_id is not None:
        query += " AND id = ?"
        params.append(business_id)
    if not refresh:
        query += " AND gbp_crosscheck_status IS NULL"
    rows = conn.execute(query, params).fetchall()

    results: list[CrosscheckResult] = []
    for i, row in enumerate(rows):
        if i > 0:
            time.sleep(0.3)  # light politeness delay against the Places API
        biz = dict(row)
        result = _crosscheck_one(biz)
        results.append(result)
        print(f"  [crosscheck] #{result.business_id} {result.name}: {result.gbp_status} — {result.note}")

        fields = dict(result.backfilled_fields)
        fields["gbp_crosscheck_status"] = result.gbp_status
        fields["gbp_crosscheck_at"] = _utcnow()
        fields["gbp_crosscheck_note"] = result.note
        fields["organic_validated"] = int(result.organic_validated)
        update_business_fields(conn, biz["id"], fields)

    return results
