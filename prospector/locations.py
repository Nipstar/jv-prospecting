"""UK town/city seed list for Prospector v2 discovery (discovery/places.py,
Phase 2). Small starter list of higher-population UK towns/cities across
regions; `--location` also accepts any freeform UK town/city/postcode
district not in this list, and `prospector discover import <csv>` lets
Andy supply his own (vertical, location) pairs in bulk rather than being
limited to this seed list.
"""
from __future__ import annotations

LOCATIONS: list[str] = [
    "London",
    "Birmingham",
    "Manchester",
    "Leeds",
    "Bristol",
    "Reading",
    "Guildford",
    "Chelmsford",
    "Southampton",
    "Brighton",
    "Cambridge",
    "Oxford",
    "Nottingham",
    "Sheffield",
    "Liverpool",
    "Newcastle",
    "York",
    "Bath",
    "St Albans",
    "Winchester",
]
