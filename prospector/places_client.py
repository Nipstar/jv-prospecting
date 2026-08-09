"""Google Places API (New) client — primary business-discovery source.

Uses the official `places.googleapis.com/v1/places:searchText` endpoint (the
new Places API, not the legacy `maps.googleapis.com/maps/api/place` one),
mirroring the pattern already established in the sibling geo-prospecting
project (see geo-prospecting/src/ingest/places.py::_run_places_api) — same
field-mask/auth-header/pagination conventions, adapted to prospector's
return shape.

`discover_businesses()` matches serpapi_client.discover_businesses()'s
signature and return shape exactly (name, address, phone, website, domain,
rating, review_count, google_place_id) so pipeline.py's downstream
processing (filtering, scoring, DB writes) doesn't need to know or care
which source found a given business.

Reviews are deliberately NOT sourced from Places here — see fetch_reviews()
docstring below and pipeline.py, which always uses serpapi_client for
reviews regardless of discovery source.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from prospector.http import ApiError, post
from prospector.config import GOOGLE_PLACES_API_KEY, GOOGLE_PLACES_BASE_URL

# Same rough radius labels as serpapi_client.RADIUS_MILES — kept here for
# signature/documentation parity, but (like SerpAPI's google_maps engine)
# the Places API Text Search endpoint has no native radius param either, so
# radius_label isn't fed into the request; it's accepted for interface
# compatibility with serpapi_client.discover_businesses.
RADIUS_MILES = {
    "5 miles": 5,
    "10 miles": 10,
    "20 miles": 20,
    "county-wide": 30,
}

_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.internationalPhoneNumber,"
    "places.websiteUri,places.rating,places.userRatingCount,"
    "places.businessStatus,nextPageToken"
)
_PAGE_SIZE = 20
_MAX_PAGES = 5  # 5 * 20 = 100 places ceiling per sector/area, same as geo-prospecting


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        netloc = urlparse(url if "://" in url else f"//{url}").netloc or urlparse(url).path
        return re.sub(r"^www\.", "", netloc).split("/")[0].lower() or None
    except Exception:
        return None


def discover_businesses(sector_term: str, area: str, radius_label: str, max_results: int) -> list[dict[str, Any]]:
    """Google Places API (New) Text Search for {sector_term} in {area}.

    Returns the same shape as serpapi_client.discover_businesses(): a list
    of dicts with name, address, phone, website, domain, rating,
    review_count, google_place_id. Non-operational places are skipped.

    Raises ApiError if GOOGLE_PLACES_API_KEY is unset or the request fails
    after retries — callers (pipeline.py) are expected to catch this and
    fall back to serpapi_client.discover_businesses.
    """
    if not GOOGLE_PLACES_API_KEY:
        raise ApiError("GOOGLE_PLACES_API_KEY is not set — check .env")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    body: dict[str, Any] = {
        "textQuery": f"{sector_term} in {area}",
        "pageSize": _PAGE_SIZE,
    }

    results: list[dict[str, Any]] = []
    for _ in range(_MAX_PAGES):
        resp = post(GOOGLE_PLACES_BASE_URL, headers=headers, json=body)
        if resp.status_code != 200:
            raise ApiError(f"Google Places discover failed: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        places = data.get("places") or []
        if not places:
            break
        for p in places:
            if (p.get("businessStatus") or "OPERATIONAL") != "OPERATIONAL":
                continue
            website = p.get("websiteUri")
            phone = p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber")
            results.append({
                "name": (p.get("displayName") or {}).get("text"),
                "address": p.get("formattedAddress"),
                "phone": phone,
                "website": website,
                "domain": _domain_from_url(website),
                "rating": p.get("rating"),
                "review_count": p.get("userRatingCount"),
                "google_place_id": p.get("id"),
            })
            if len(results) >= max_results:
                return results[:max_results]

        token = data.get("nextPageToken")
        if not token:
            break
        body = {**body, "pageToken": token}

    return results[:max_results]


def fetch_reviews(place_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Not used by the pipeline — kept only as a documented non-default.

    Google's Places Details endpoint caps reviews at 5 (vs. SerpAPI's
    google_maps_reviews engine, which returns ~20). Since pain-flag
    detection quality depends on review *volume*, prospector always uses
    serpapi_client.fetch_reviews for the reviews step, regardless of
    whether Google Places or SerpAPI was the discovery source that found
    the business (google_place_id is a standard Google place ID usable by
    either API). This function is a placeholder should a Places-based
    reviews fallback ever be wanted, and intentionally raises to make that
    explicit rather than silently returning a thin result set.
    """
    raise NotImplementedError(
        "places_client.fetch_reviews is intentionally unimplemented — "
        "prospector always uses serpapi_client.fetch_reviews for reviews "
        "(Places Details caps reviews at 5, too thin for pain-flag detection). "
        "See places_client.py module docstring."
    )
