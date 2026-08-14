"""Checkatrade.com discovery source — additional discovery pass alongside
Google Places, Yell.com, and SerpAPI organic search.

Added to replace/supplement discovery/yell.py: the Yell actor
(jungle_synthesizer/yell-uk-business-directory-scraper) is confirmed
broken for multi-word keywords (its own URL-builder 404s against real
Yell.com — see README "Known limitations"), which makes it low-value for
most of prospector's verticals ("heating plumbing and electrical
contractors" etc. are all multi-word). Checkatrade is UK's other major
trade directory and arguably more relevant than Yell for prospector's
trade-heavy verticals (HVAC, plumbing, electrical, roofing, driveways,
garage/window/conservatory installers) — Checkatrade doesn't cover
prospector's non-trade verticals (solicitors, accountants, dental, vets,
funeral directors) at all, so this source will legitimately return 0 for
those, same as Yell/organic returning thin results for a query they're
not suited to.

Uses the Apify actor `trev0n/checkatrade-scraper` (confirmed real and
functioning via Apify's actor-search API, then live-tested with a real 3-
item call, rather than assumed — see scoring_config.py's
APIFY_CHECKATRADE_* block for the full verification notes), called via
the same run-sync-get-dataset-items REST pattern already established by
yell.py / enrichers/site.py's Apify fallback layer — same APIFY_TOKEN,
same endpoint shape, different actor/input/timeout.

`discover_businesses()` matches the other three sources' signature/return
shape exactly (name, address, phone, website, domain, rating,
review_count, google_place_id) so discovery/places.py's dedupe/enrichment
pipeline doesn't need to know or care which source found a given
business. Differences, both additive:

  - google_place_id is always None (Checkatrade has no Google Places ID).
  - checkatrade_listing_id carries Checkatrade's own profile URL instead,
    for provenance/audit — new, additive `businesses.checkatrade_listing_id`
    column (migration v10, see db.py).
  - rating is normalised from Checkatrade's native 0-10 scale to Google's
    0-5 scale (divided by CHECKATRADE_RATING_SCALE_DIVISOR) so
    scoring.py's avg_rating thresholds (tuned for Places/SerpAPI's 0-5
    scale) aren't silently miscalibrated for Checkatrade-sourced rows.

Trade-slug matching (best-effort, not a bug)
---------------------------------------------
Checkatrade's own actor restricts its `trade` input to an enum of ~1600
official category slugs. prospector's vertical search terms are freeform
("heating plumbing and electrical contractors") and won't reliably match
one. Rather than hardcode a sector-term -> official-slug mapping (brittle,
incomplete), this module builds a naive best-effort slug (title-case each
word, hyphen-join) and passes it via the actor's `searchUrls` input, which
the actor's own README confirms accepts arbitrary slugs (bypasses the
`trade` enum restriction). Live-tested: a well-formed real slug
("Air-Conditioning-Installation") returns real structured results; a
made-up/unmatched multi-word slug still completes successfully with 0
items rather than erroring — i.e. this source fails soft on a slug
mismatch exactly like it fails soft on a genuinely trade-mismatched
vertical (solicitors, vets, etc.), no special-casing needed.

Fails soft, not hard
---------------------
Same contract as yell.py: raises ApiError only for a genuinely unusable
config (no APIFY_TOKEN). A failed/empty actor run (bad slug, no results
for this trade/location) is treated as "this source found nothing this
time", logged, and returns [] — Checkatrade is an *additional* pass
alongside Places, not the sole source.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from prospector.config import APIFY_TOKEN
from prospector.http import ApiError
from prospector.places_client import _domain_from_url
from prospector.scoring_config import (
    APIFY_ACTOR_RUN_ENDPOINT,
    APIFY_CHECKATRADE_ACTOR_ID,
    APIFY_CHECKATRADE_MAX_ITEMS_CAP,
    APIFY_CHECKATRADE_TIMEOUT_SECONDS,
    CHECKATRADE_RATING_SCALE_DIVISOR,
)

# Same rough radius labels as the sibling discovery sources — kept for
# interface parity; Checkatrade's search has no native radius param
# either, so radius_label isn't fed into the actor input.
RADIUS_MILES = {
    "5 miles": 5,
    "10 miles": 10,
    "20 miles": 20,
    "county-wide": 30,
}

_SLUG_STOPWORDS = {"of", "the"}  # dropped from the naive slug, Checkatrade's real slugs never include them


def _slugify(sector_term: str) -> str:
    """Best-effort "{sector term}" -> "Checkatrade-Style-Slug" — see module
    docstring "Trade-slug matching" for why this is deliberately naive
    rather than a hardcoded mapping."""
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", sector_term) if w and w.lower() not in _SLUG_STOPWORDS]
    return "-".join(w.capitalize() for w in words) or sector_term.strip().replace(" ", "-")


def _location_slug(area: str) -> str:
    return "-".join(w for w in re.split(r"\s+", area.strip()) if w)


def discover_businesses(sector_term: str, area: str, radius_label: str, max_results: int) -> list[dict[str, Any]]:
    """Checkatrade.com search for {sector_term} in {area}.

    Returns the same shape as the other discovery sources: name, address
    (city/areaServed — Checkatrade doesn't expose a full street address),
    phone, website, domain, rating (normalised to 0-5), review_count,
    google_place_id (always None), checkatrade_listing_id.

    Raises ApiError only if APIFY_TOKEN is unset. Any other failure (actor
    run failed, or returned zero items — e.g. sector_term's naive slug
    doesn't match a real Checkatrade trade) is caught, logged to stdout,
    and returns [] — see module docstring "Fails soft, not hard".
    """
    if not APIFY_TOKEN:
        raise ApiError("APIFY_TOKEN is not set — check .env")

    trade_slug = _slugify(sector_term)
    location_slug = _location_slug(area)
    search_url = f"https://www.checkatrade.com/Search/{trade_slug}/in/{location_slug}/"

    endpoint = APIFY_ACTOR_RUN_ENDPOINT.format(actor_id=APIFY_CHECKATRADE_ACTOR_ID)
    payload = {
        "searchUrls": [{"url": search_url}],
        "maxItems": min(max_results, APIFY_CHECKATRADE_MAX_ITEMS_CAP),
        "extractReviews": False,  # we only need the listing summary, not full review text, for discovery
        "requirePhone": False,  # filter at scoring time, not discovery time — see other sources' convention
    }

    try:
        resp = httpx.post(
            endpoint,
            params={"token": APIFY_TOKEN},
            json=payload,
            timeout=APIFY_CHECKATRADE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        items = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        print(f"  [checkatrade] discover failed for {sector_term!r} in {area!r} (slug={trade_slug!r}): {exc} — treating as 0 Checkatrade results.")
        return []

    if not items:
        return []

    results: list[dict[str, Any]] = []
    for item in items:
        name = item.get("name")
        if not name:
            continue
        website = item.get("website")
        phone = item.get("phone") or (item.get("phones") or [None])[0]
        address_parts = [p for p in (item.get("city"), *(item.get("areaServed") or [])) if p]
        address = ", ".join(dict.fromkeys(address_parts)) if address_parts else None  # dict.fromkeys: order-preserving de-dupe
        raw_rating = item.get("rating")
        rating = round(raw_rating / CHECKATRADE_RATING_SCALE_DIVISOR, 2) if isinstance(raw_rating, (int, float)) else None
        results.append({
            "name": name,
            "address": address,
            "phone": phone,
            "website": website,
            "domain": _domain_from_url(website),
            "rating": rating,
            "review_count": item.get("reviewsCount"),
            "google_place_id": None,
            "checkatrade_listing_id": item.get("url"),
        })
        if len(results) >= max_results:
            break

    return results[:max_results]
