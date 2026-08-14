"""Yell.com discovery source — additional discovery pass alongside Google
Places (prospector/places_client.py), added per Andy's request to catch
businesses that don't surface well on Google Places/Maps.

Uses the Apify actor `jungle_synthesizer/yell-uk-business-directory-scraper`
(confirmed real via Apify's actor-search API rather than assumed — see
prospector/scoring_config.py's APIFY_YELL_* block for the verification
notes and live-test caveats), called via the same run-sync-get-dataset-items
REST pattern already established in enrichers/site.py's Apify fallback
layer (layer 3 of the site-fetch escalation chain) — same APIFY_TOKEN,
same endpoint shape, different actor/input/timeout.

`discover_businesses()` deliberately matches places_client.discover_businesses()
/ serpapi_client.discover_businesses()'s signature and return shape (name,
address, phone, website, domain, rating, review_count, google_place_id) so
discovery/places.py's dedupe/enrichment pipeline doesn't need to know or
care which source found a given business. Two differences, both additive:

  - google_place_id is always None (Yell has no Google Places ID).
  - yell_listing_id carries Yell's own profile URL instead, for
    provenance/audit — new, additive `businesses.yell_listing_id` column
    (migration v9, see db.py).

Fails soft, not hard
---------------------
Unlike places_client.discover_businesses (which raises ApiError so
pipeline.py can fall back to serpapi_client), this module raises ApiError
only for a genuinely unusable config (no APIFY_TOKEN). A failed/empty
actor run (e.g. Yell 404s on a location string it doesn't recognise —
live-tested with "South London", see scoring_config.py) is treated as
"this source found nothing this time", logged, and returns an empty list
— because Yell is an *additional* pass alongside Places, not the sole
source, a single bad location/keyword combination for Yell specifically
should never block the Places-sourced results already found in the same
`discover run` invocation.
"""
from __future__ import annotations

from typing import Any

import httpx

from prospector.config import APIFY_TOKEN
from prospector.http import ApiError
from prospector.places_client import _domain_from_url
from prospector.scoring_config import (
    APIFY_ACTOR_RUN_ENDPOINT,
    APIFY_YELL_ACTOR_ID,
    APIFY_YELL_MAX_ITEMS_CAP,
    APIFY_YELL_TIMEOUT_SECONDS,
)

# Same rough radius labels as places_client.RADIUS_MILES/serpapi_client.RADIUS_MILES
# — kept for interface parity; Yell's search has no native radius param
# either, so radius_label isn't fed into the actor input.
RADIUS_MILES = {
    "5 miles": 5,
    "10 miles": 10,
    "20 miles": 20,
    "county-wide": 30,
}


def discover_businesses(sector_term: str, area: str, radius_label: str, max_results: int) -> list[dict[str, Any]]:
    """Yell.com business-directory search for {sector_term} in {area}.

    Returns the same shape as places_client.discover_businesses(): a list
    of dicts with name, address, phone, website, domain, rating,
    review_count, google_place_id (always None), yell_listing_id.

    Raises ApiError only if APIFY_TOKEN is unset. Any other failure (actor
    run failed, timed out, or returned zero items) is caught, logged to
    stdout, and returns [] — see module docstring "Fails soft, not hard".
    """
    if not APIFY_TOKEN:
        raise ApiError("APIFY_TOKEN is not set — check .env")

    endpoint = APIFY_ACTOR_RUN_ENDPOINT.format(actor_id=APIFY_YELL_ACTOR_ID)
    payload = {
        "keywords": sector_term,
        "location": area,
        "maxItems": min(max_results, APIFY_YELL_MAX_ITEMS_CAP),
        # This actor is built on an Apify "standby prompt" template that
        # requires these sp_* fields even though they're not part of its
        # real scraping params (confirmed via the actor's exampleRunInput —
        # omitting them causes a 400 Bad Request, not a graceful default).
        "sp_intended_usage": "UK business prospecting — discovering independent local service firms for outreach.",
        "sp_improvement_suggestions": "None at this time.",
        "sp_contact": "andy@antekautomation.com",
    }

    try:
        resp = httpx.post(
            endpoint,
            params={"token": APIFY_TOKEN},
            json=payload,
            timeout=APIFY_YELL_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        items = resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        print(f"  [yell] discover failed for {sector_term!r} in {area!r}: {exc} — treating as 0 Yell results.")
        return []

    if not items:
        return []

    results: list[dict[str, Any]] = []
    for item in items:
        name = item.get("business_name")
        if not name:
            continue
        website = item.get("website")
        phone = item.get("phone") or item.get("mobile_phone")
        address_parts = [p for p in (item.get("address"), item.get("city"), item.get("postcode")) if p]
        address = ", ".join(address_parts) if address_parts else None
        results.append({
            "name": name,
            "address": address,
            "phone": phone,
            "website": website,
            "domain": _domain_from_url(website),
            "rating": item.get("rating"),
            "review_count": item.get("reviews_count"),
            "google_place_id": None,
            "yell_listing_id": item.get("profile_url"),
        })
        if len(results) >= max_results:
            break

    return results[:max_results]
