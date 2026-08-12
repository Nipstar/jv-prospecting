"""Companies House API client — ownership/PSC lookup.

https://api.company-information.service.gov.uk
Used to filter out group-owned / corporate-controlled businesses so outreach
targets are genuinely independent owner-operators (decision-maker reachable,
budget owned locally).
"""
from __future__ import annotations

from typing import Any

from requests.auth import HTTPBasicAuth

from prospector.http import ApiError, get
from prospector.config import COMPANIES_HOUSE_API_KEY, COMPANIES_HOUSE_BASE_URL


def _auth() -> HTTPBasicAuth:
    # Companies House uses HTTP Basic auth with the API key as username, blank password.
    return HTTPBasicAuth(COMPANIES_HOUSE_API_KEY, "")


def search_company(name: str) -> dict[str, Any] | None:
    """Best-effort match: search by name, return the top result or None."""
    if not COMPANIES_HOUSE_API_KEY:
        raise ApiError("COMPANIES_HOUSE_API_KEY is not set — check .env")
    resp = get(
        f"{COMPANIES_HOUSE_BASE_URL}/search/companies",
        params={"q": name, "items_per_page": 5},
        auth=_auth(),
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ApiError(f"Companies House search failed: HTTP {resp.status_code}: {resp.text[:300]}")
    items = resp.json().get("items") or []
    return items[0] if items else None


def get_company_profile(company_number: str) -> dict[str, Any] | None:
    """Full company profile — used by discovery/places.py (Phase 2) for
    incorporation date + company status, distinct from check_ownership()
    below (which only cares about PSC/officer ownership structure)."""
    resp = get(
        f"{COMPANIES_HOUSE_BASE_URL}/company/{company_number}",
        auth=_auth(),
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ApiError(f"Companies House profile lookup failed: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def get_psc(company_number: str) -> list[dict[str, Any]]:
    """Persons/entities with significant control for a company number."""
    resp = get(
        f"{COMPANIES_HOUSE_BASE_URL}/company/{company_number}/persons-with-significant-control",
        auth=_auth(),
    )
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise ApiError(f"Companies House PSC lookup failed: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("items") or []


def get_officers(company_number: str) -> list[dict[str, Any]]:
    resp = get(
        f"{COMPANIES_HOUSE_BASE_URL}/company/{company_number}/officers",
        auth=_auth(),
    )
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise ApiError(f"Companies House officers lookup failed: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("items") or []


def check_ownership(business_name: str) -> dict[str, Any]:
    """Look up a business by name and flag group/corporate ownership.

    is_group_owned = True if any PSC is a corporate entity ("corporate-entity"
    kind) rather than an individual, since that signals the business is
    controlled by a parent company/group rather than an independent owner.
    Also captures a director_name from the first natural-person officer, for
    the businesses.director_name column.

    Returns dict: {company_number, director_name, is_group_owned} — all
    fields None/False if no confident match is found (fails open, i.e. does
    not block the business from the pipeline; the wizard's ownership filter
    only excludes on a *positive* group-owned match).
    """
    result: dict[str, Any] = {
        "company_number": None,
        "director_name": None,
        "is_group_owned": False,
    }
    match = search_company(business_name)
    if not match:
        return result

    company_number = match.get("company_number")
    result["company_number"] = company_number
    if not company_number:
        return result

    try:
        pscs = get_psc(company_number)
    except ApiError:
        pscs = []
    if any((p.get("kind") or "").startswith("corporate-entity") for p in pscs):
        result["is_group_owned"] = True

    try:
        officers = get_officers(company_number)
    except ApiError:
        officers = []
    for officer in officers:
        if officer.get("resigned_on"):
            continue
        role = (officer.get("officer_role") or "").lower()
        if role in ("director", "llp-member") and officer.get("name"):
            result["director_name"] = officer["name"]
            break

    return result
