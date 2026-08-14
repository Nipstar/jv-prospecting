"""SerpAPI organic Google search — third discovery source, additional to
Google Places and Yell.com.

Distinct from serpapi_client.discover_businesses() (engine=google_maps,
the Places-fallback local-pack engine already wired into pipeline.py — see
that module's docstring, left unchanged by this addition) — this uses
engine=google, the *regular organic* web-search results, 2-3 pages deep
(SerpAPI paginates organic results via `start`, 10 results/page). The
intent (per Andy) is to catch genuinely independent business websites that
rank organically for "{vertical} {location}"-type queries but don't
surface well on Google Places/Maps or Yell — not to re-scrape directory/
aggregator pages we already treat as their own thing (Yell itself, plus
Checkatrade/Trustpilot/Yelp/Bark/etc.), so those domains are filtered out
of the organic result set before it's returned (see EXCLUDED_DOMAINS).

EXCLUDED_DOMAINS only catches known aggregator domains. Live sweep found
organic also surfaces junk on domains we've never seen before — job
boards, course-listing pages, "Top N Companies in X" blog listicles — and
plain wrong-vertical businesses on real domains (e.g. a car-AC specialist
showing up for "air conditioning" searches, since that term is ambiguous
between building and vehicle trades). Each surviving result now also runs
through discovery.validate.validate_organic_result() — a title-pattern
check plus a one-shot page-content check for schema.org LocalBusiness
markup / blog signals / directory-style link density — before being kept.
See prospector/discovery/validate.py for the full rationale.

Coverage caveat (documented, not a bug): organic search results give a
title/link/snippet, not a structured business record — there is no
phone/address/rating field the way Places/SerpAPI-maps/Yell all provide
one. `discover_businesses()` below extracts a UK phone number from the
snippet text on a best-effort basis (same PHONE_PATTERN regex used by
enrichers/site.py's phone-dependency check) and otherwise leaves
address/rating/review_count as None — a genuinely weaker record than the
other two sources, expected and fine for prospecting purposes since
downstream enrichment (Companies House, reviews fetch if a place_id later
turns up, site fetch) still runs identically regardless of source.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from prospector.config import GL, GOOGLE_DOMAIN, HL, SERPAPI_BASE_URL, SERPAPI_KEY
from prospector.discovery.validate import validate_organic_result
from prospector.http import ApiError, get
from prospector.scoring_config import PHONE_PATTERN

RADIUS_MILES = {
    "5 miles": 5,
    "10 miles": 10,
    "20 miles": 20,
    "county-wide": 30,
}

_RESULTS_PER_PAGE = 10  # SerpAPI engine=google organic results per page
_MAX_PAGES = 3  # 3 * 10 = 30 organic results ceiling per sector/area, per spec ("2-3 pages deep")

# Big directories/aggregators/review sites we don't want organic results
# from — either because we already query them directly as their own
# discovery source (Yell), or because they're not an independent business
# (Facebook/Instagram profile pages, marketplaces, review aggregators,
# other trade directories). Matched as a domain substring, case-
# insensitive. Extend as Andy spots more directory noise in real runs.
EXCLUDED_DOMAINS: list[str] = [
    "yell.com",
    "checkatrade.com",
    "trustpilot.com",
    "yelp.com",
    "yelp.co.uk",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "bark.com",
    "mybuilder.com",
    "ratedpeople.com",
    "threebestrated.co.uk",
    "thomsonlocal.com",
    "cylex-uk.co.uk",
    "192.com",
    "freeindex.co.uk",
    "houzz.co.uk",
    "which.co.uk",
    "trustatrader.com",
    "hamuch.com",
    # UK trade-body member registers, not individual businesses — surfaced
    # in live testing (an organic result for "electricians in Guildford"
    # landed on niceic.com, the trade register, not a specific firm).
    "niceic.com",
    "napit.org.uk",
    "elecsa.co.uk",
    "google.com",
    "google.co.uk",
    "maps.google.com",
    "scoot.co.uk",
    "yalwa.co.uk",
    "hotfrog.co.uk",
    "brownbook.net",
    "tupalo.com",
    "foursquare.com",
    "indeed.com",
    "reddit.com",
    "wikipedia.org",
    "gov.uk",
    "nhs.uk",
    "companieshouse.gov.uk",
]


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        netloc = urlparse(url if "://" in url else f"//{url}").netloc or urlparse(url).path
        return re.sub(r"^www\.", "", netloc).split("/")[0].lower() or None
    except Exception:
        return None


def _is_excluded(domain: str | None) -> bool:
    if not domain:
        return False
    return any(domain == d or domain.endswith(f".{d}") for d in EXCLUDED_DOMAINS)


def _clean_title(title: str | None) -> str | None:
    """Organic titles are often "Business Name | Tagline - Site" — take the
    first pipe/dash-delimited segment as a best-effort business name.
    Best-effort only; not a full title-cleaning NLP pass."""
    if not title:
        return None
    for sep in (" | ", " – ", " — ", " - "):
        if sep in title:
            return title.split(sep)[0].strip()
    return title.strip()


def _extract_phone(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(PHONE_PATTERN, text)
    return m.group(0).strip() if m else None


def discover_businesses(sector_term: str, area: str, radius_label: str, max_results: int) -> list[dict[str, Any]]:
    """SerpAPI organic Google search ("{sector_term} in {area}"), 2-3 pages
    deep, directory/aggregator domains filtered out.

    Returns the same shape as places_client.discover_businesses(): name,
    address (always None — not available from organic results), phone
    (best-effort, extracted from the snippet), website, domain, rating
    (always None), review_count (always None), google_place_id (always
    None).
    """
    if not SERPAPI_KEY:
        raise ApiError("SERPAPI_KEY is not set — check .env")

    query = f"{sector_term} in {area}"
    results: list[dict[str, Any]] = []
    seen_domains: set[str] = set()

    for page in range(_MAX_PAGES):
        if len(results) >= max_results:
            break
        params = {
            "engine": "google",
            "q": query,
            "google_domain": GOOGLE_DOMAIN,
            "hl": HL,
            "gl": GL,
            "start": page * _RESULTS_PER_PAGE,
            "num": _RESULTS_PER_PAGE,
            "api_key": SERPAPI_KEY,
        }
        resp = get(SERPAPI_BASE_URL, params=params)
        if resp.status_code != 200:
            raise ApiError(f"SerpAPI organic discover failed: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        organic_results = data.get("organic_results") or []
        if not organic_results:
            break

        for item in organic_results:
            link = item.get("link")
            domain = _domain_from_url(link)
            if not domain or _is_excluded(domain):
                continue
            if domain in seen_domains:
                # Same business's homepage can rank for more than one
                # result on the same SERP (e.g. homepage + a service
                # subpage) — dedupe within this source before it even
                # reaches the DB-level phone/domain dedupe.
                continue

            name = _clean_title(item.get("title"))
            is_valid, reject_reason = validate_organic_result(name, link)
            if not is_valid:
                print(f"  [organic] skipped (content filter): {name!r} — {reject_reason}")
                continue

            seen_domains.add(domain)

            snippet = item.get("snippet")
            results.append({
                "name": name,
                "address": None,
                "phone": _extract_phone(snippet),
                "website": link,
                "domain": domain,
                "rating": None,
                "review_count": None,
                "google_place_id": None,
            })
            if len(results) >= max_results:
                break

        if len(organic_results) < _RESULTS_PER_PAGE:
            break

    return results[:max_results]
