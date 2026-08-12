"""Vertical seed list for Prospector v2 — UK High-Ticket Firms,
Review-Based Targeting (discovery/places.py, Phase 2).

This is a *separate* config from prospector/trade_sectors.py, not a
replacement of it. trade_sectors.py backs the legacy `prospector run`
wizard/pipeline (ad-spend-model era, keyed by ticket_size_estimate — that
field has no meaning for the review-based model and the wizard still
imports it, so removing/repurposing trade_sectors.py would break the
existing `prospector run` command that the 8 real historical runs used).
This file is the vertical list Andy specified for the v2 rebuild's
discovery module, used only by `prospector discover`.

Each entry: slug -> {name, google_search_term}. `google_search_term` is
fed into the "{term} in {location}" Places text-search query pattern.
"""
from __future__ import annotations

VERTICALS: dict[str, dict[str, str]] = {
    "estate_agents_lettings": {
        "name": "Estate agents and lettings",
        "google_search_term": "estate agents and lettings",
    },
    "solicitors_conveyancers": {
        "name": "Solicitors and conveyancers",
        "google_search_term": "solicitors and conveyancers",
    },
    "accountants": {
        "name": "Accountants",
        "google_search_term": "accountants",
    },
    "heating_plumbing_electrical": {
        "name": "Heating / plumbing / electrical (larger firms, not sole traders)",
        "google_search_term": "heating plumbing and electrical contractors",
    },
    "roofing_damp_driveways": {
        "name": "Roofing, damp proofing, driveways",
        "google_search_term": "roofing damp proofing and driveways specialists",
    },
    "dental_cosmetic_aesthetic": {
        "name": "Private dental / cosmetic / aesthetic clinics",
        "google_search_term": "private dental cosmetic and aesthetic clinic",
    },
    "vets": {
        "name": "Vets",
        "google_search_term": "veterinary practice",
    },
    "funeral_directors": {
        "name": "Funeral directors",
        "google_search_term": "funeral directors",
    },
    "garage_window_conservatory": {
        "name": "Garage door / window / conservatory installers",
        "google_search_term": "garage door window and conservatory installers",
    },
}


def resolve_search_term(vertical: str) -> tuple[str, str]:
    """Resolve a --vertical CLI value to (vertical_label, search_term).

    Accepts a VERTICALS slug (e.g. "vets"), a VERTICALS display name
    (case-insensitive, e.g. "Vets"), or arbitrary freeform text — falls
    through to using the raw input as both label and search term so Andy
    isn't locked out of prospecting a vertical that isn't in the seed list
    yet.
    """
    key = vertical.strip()
    if key in VERTICALS:
        return VERTICALS[key]["name"], VERTICALS[key]["google_search_term"]
    lowered = key.lower()
    for info in VERTICALS.values():
        if info["name"].lower() == lowered:
            return info["name"], info["google_search_term"]
    return key, key
