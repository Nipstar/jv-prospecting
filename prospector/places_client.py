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

from prospector.http import ApiError, get, post
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
        # Restrict results to the UK. Without this, ambiguous/colloquial
        # area names (e.g. "South London", "Hampshire") let same-named
        # results from other countries leak into live runs — a Canadian
        # "Roy Inch & Sons" (enercare.ca) and a Kentucky, USA "Smith
        # Heating & Cooling" both matched a "South London" query before
        # this fix (see README "Google Places country restriction").
        # `regionCode` is the New Places API (v1) Text Search request-body
        # field for this — a CLDR two-letter region code that restricts/
        # biases results to that region (distinct from the legacy Places
        # API's `region` query param). Confirmed live: re-running the
        # exact queries above with regionCode=GB returns zero non-UK
        # results.
        "regionCode": "GB",
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


_DETAILS_FIELD_MASK = (
    "id,displayName,rating,userRatingCount,websiteUri,businessStatus,"
    "regularOpeningHours,reviews"
)


def get_place_details(place_id: str) -> dict[str, Any]:
    """Google Places API (New) Place Details fetch — used by
    enrichers/reviews.py (Phase 3) for review-profile scoring.

    Distinct from fetch_reviews() below: this uses the Details endpoint
    (`places.googleapis.com/v1/places/{place_id}`), which is what actually
    returns per-place review snippets (capped at 5 by Google — see Phase 3
    docs), rating, userRatingCount, websiteUri, and regularOpeningHours (used
    for the weak_gbp "unclaimed-looking profile" heuristic). This is a
    different, additive use case from fetch_reviews()'s NotImplementedError
    below, which is specifically about *not* using Places as the reviews
    source for the legacy pain-flag pipeline (which needs ~20 reviews, not
    5) — Phase 3's review_target_score is a different, new feature that
    explicitly wants Places' 5-review snippet set, caveat documented and
    accepted (see enrichers/reviews.py module docstring).

    Returns a dict with keys: rating, user_ratings_total, website, has_hours,
    business_status, reviews (list of {rating, text, publish_time}).
    Raises ApiError on failure — callers should catch and treat as "no
    listing found" per the weak_gbp heuristic.
    """
    if not GOOGLE_PLACES_API_KEY:
        raise ApiError("GOOGLE_PLACES_API_KEY is not set — check .env")

    url = f"https://places.googleapis.com/v1/places/{place_id}"
    headers = {
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": _DETAILS_FIELD_MASK,
    }
    resp = get(url, headers=headers)
    if resp.status_code != 200:
        raise ApiError(f"Google Places details failed: HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()

    reviews = []
    for r in (data.get("reviews") or []):
        reviews.append({
            "rating": r.get("rating"),
            "text": (r.get("text") or {}).get("text"),
            "publish_time": r.get("publishTime"),
        })

    return {
        "rating": data.get("rating"),
        "user_ratings_total": data.get("userRatingCount"),
        "website": data.get("websiteUri"),
        "has_hours": bool(data.get("regularOpeningHours")),
        "business_status": data.get("businessStatus"),
        "reviews": reviews,
    }


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


def find_place_by_name(name: str, location: str) -> dict[str, Any] | None:
    """Targeted single-result Places Text Search — "does a GBP listing for
    *this specific business* actually exist", not a discovery pass.

    Added for the organic-search cross-check (prospector/enrichers/
    crosscheck.py): an organic-sourced business has no google_place_id
    because it was never a Places result for the generic "{vertical} in
    {location}" query — but it might still have a real GBP listing that
    just didn't rank inside our page-size/page-count ceiling, or that
    surfaces under different phrasing than the vertical search term (e.g.
    "Cool Electrics" the business name vs. "air conditioning" the
    vertical). Searching "{name} {location}" directly is a much more
    targeted query than the discovery pass and will catch those.

    Reuses discover_businesses()'s exact request shape (same endpoint,
    field mask, regionCode=GB restriction) but as a single-page, top-1
    lookup rather than a paginated discovery sweep — this is a lookup, not
    a discovery source, so it isn't registered in discovery/places.py's
    DISCOVERY_SOURCES.

    Returns the same per-place dict shape as discover_businesses() (name,
    address, phone, website, domain, rating, review_count,
    google_place_id), or None if Places returns no results or a
    non-operational-only result set. Raises ApiError if
    GOOGLE_PLACES_API_KEY is unset or the request fails after retries —
    callers (crosscheck.py) catch this and treat it as "couldn't check,"
    not "no GBP listing exists."
    """
    if not GOOGLE_PLACES_API_KEY:
        raise ApiError("GOOGLE_PLACES_API_KEY is not set — check .env")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    body: dict[str, Any] = {
        "textQuery": f"{name} {location}",
        "pageSize": 3,  # top few, not a full discovery page — we just want the best match
        "regionCode": "GB",
    }
    resp = post(GOOGLE_PLACES_BASE_URL, headers=headers, json=body)
    if resp.status_code != 200:
        raise ApiError(f"Google Places name-lookup failed: HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    places = data.get("places") or []
    for p in places:
        if (p.get("businessStatus") or "OPERATIONAL") != "OPERATIONAL":
            continue
        website = p.get("websiteUri")
        phone = p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber")
        return {
            "name": (p.get("displayName") or {}).get("text"),
            "address": p.get("formattedAddress"),
            "phone": phone,
            "website": website,
            "domain": _domain_from_url(website),
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"),
            "google_place_id": p.get("id"),
        }
    return None
