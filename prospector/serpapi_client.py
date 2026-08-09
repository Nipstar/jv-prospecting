"""SerpAPI client — Google Local/Maps discovery + Google Maps Reviews.

https://serpapi.com/search with engine=google_maps and
engine=google_maps_reviews.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from prospector.http import ApiError, get
from prospector.config import GL, GOOGLE_DOMAIN, HL, SERPAPI_BASE_URL, SERPAPI_KEY

RADIUS_MILES = {
    "5 miles": 5,
    "10 miles": 10,
    "20 miles": 20,
    "county-wide": 30,  # rough proxy — SerpAPI's google_maps engine has no
                        # native radius param; we widen the query instead.
}


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        netloc = urlparse(url if "://" in url else f"//{url}").netloc or urlparse(url).path
        return re.sub(r"^www\.", "", netloc).split("/")[0].lower() or None
    except Exception:
        return None


def discover_businesses(sector_term: str, area: str, radius_label: str, max_results: int) -> list[dict[str, Any]]:
    """Google Maps search for {sector_term} in {area}.

    Returns a list of raw business dicts: name, address, phone, website,
    rating, review_count, place_id.
    """
    if not SERPAPI_KEY:
        raise ApiError("SERPAPI_KEY is not set — check .env")

    query = f"{sector_term} in {area}"
    results: list[dict[str, Any]] = []
    start = 0
    page_size = 20  # SerpAPI google_maps engine returns ~20 local results/page

    while len(results) < max_results:
        params = {
            "engine": "google_maps",
            "type": "search",
            "q": query,
            "google_domain": GOOGLE_DOMAIN,
            "hl": HL,
            "gl": GL,
            "start": start,
            "api_key": SERPAPI_KEY,
        }
        resp = get(SERPAPI_BASE_URL, params=params)
        if resp.status_code != 200:
            raise ApiError(f"SerpAPI discover failed: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        local_results = data.get("local_results") or []
        if not local_results:
            break
        for item in local_results:
            website = item.get("website")
            results.append({
                "name": item.get("title"),
                "address": item.get("address"),
                "phone": item.get("phone"),
                "website": website,
                "domain": _domain_from_url(website),
                "rating": item.get("rating"),
                "review_count": item.get("reviews"),
                "google_place_id": item.get("place_id"),
            })
            if len(results) >= max_results:
                break
        if len(local_results) < page_size:
            break
        start += page_size

    return results[:max_results]


def fetch_reviews(place_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Latest ~`limit` reviews for a place_id via engine=google_maps_reviews."""
    if not SERPAPI_KEY:
        raise ApiError("SERPAPI_KEY is not set — check .env")
    if not place_id:
        return []

    params = {
        "engine": "google_maps_reviews",
        "place_id": place_id,
        "hl": HL,
        "sort_by": "newestFirst",
        "api_key": SERPAPI_KEY,
    }
    resp = get(SERPAPI_BASE_URL, params=params)
    if resp.status_code != 200:
        raise ApiError(f"SerpAPI reviews failed: HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    reviews = data.get("reviews") or []
    out = []
    for r in reviews[:limit]:
        out.append({
            "rating": r.get("rating"),
            "text": r.get("snippet") or r.get("text"),
            "review_date": r.get("date"),
        })
    return out
