"""Interactive wizard — the "series of asks" that configures a run.

Uses questionary for all prompts, in the exact order specified:
  1. Area
  2. Radius
  3. Trade sectors (multi-select + custom)
  4. Minimum review count
  5. Minimum rating
  6. Max businesses per sector
  7. Ownership filter
  8. Dry run?

After the wizard, prints a cost estimate and asks for final confirmation.

The ad-qualification question (Meta/Google ad spend rule) was removed in
the "Prospector v2" rebuild's Phase 1, along with the rest of the ad-spend
module — see prospector/pipeline.py and prospector/scoring.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import questionary
from questionary import Choice, Separator

from prospector.trade_sectors import TRADE_SECTORS, sectors_by_category

RADIUS_CHOICES = ["5 miles", "10 miles", "20 miles", "county-wide"]


@dataclass
class RunConfig:
    area: str
    radius: str
    sector_slugs: list[str]
    custom_sectors: list[str]
    min_review_count: int
    min_rating: float
    max_per_sector: int
    exclude_group_owned: bool
    dry_run: bool
    notes: str = ""

    @property
    def all_sector_terms(self) -> list[tuple[str, str]]:
        """List of (label, google_search_term) for every selected sector,
        including custom free-text ones (label == search term for custom)."""
        terms = [(TRADE_SECTORS[s]["name"], TRADE_SECTORS[s]["google_search_term"]) for s in self.sector_slugs]
        terms += [(c, c) for c in self.custom_sectors]
        return terms


def _positive_int(default: int):
    def validate(text: str) -> bool | str:
        text = text.strip() or str(default)
        try:
            v = int(text)
        except ValueError:
            return "Enter a whole number"
        return True if v > 0 else "Must be greater than 0"
    return validate


def _positive_float(default: float):
    def validate(text: str) -> bool | str:
        text = text.strip() or str(default)
        try:
            v = float(text)
        except ValueError:
            return "Enter a number"
        return True if v >= 0 else "Must be zero or greater"
    return validate


def run_wizard() -> RunConfig:
    area = questionary.text(
        "1) Area — UK town/city or postcode district (e.g. 'Basingstoke' or 'RG21'):"
    ).ask()
    if not area or not area.strip():
        raise SystemExit("Area is required — aborting.")
    area = area.strip()

    radius = questionary.select(
        "2) Radius:",
        choices=RADIUS_CHOICES,
        default="10 miles",
    ).ask()

    grouped = sectors_by_category()
    sector_choices: list = []
    for category, entries in grouped.items():
        if not entries:
            continue
        sector_choices.append(Separator(f"-- {category} --"))
        for slug, info in entries:
            sector_choices.append(Choice(title=f"{info['name']} ({info['ticket_size_estimate']})", value=slug))

    selected_slugs = questionary.checkbox(
        "3) Trade sectors — select all that apply (space to toggle, enter to confirm):",
        choices=sector_choices,
    ).ask() or []

    custom_sectors: list[str] = []
    add_custom = questionary.confirm("   Add custom sector(s) not on the list?", default=False).ask()
    if add_custom:
        raw = questionary.text(
            "   Enter custom sector(s), comma-separated:"
        ).ask() or ""
        custom_sectors = [s.strip() for s in raw.split(",") if s.strip()]

    if not selected_slugs and not custom_sectors:
        raise SystemExit("No trade sectors selected — aborting.")

    min_review_raw = questionary.text(
        "4) Minimum review count:", default="10", validate=_positive_int(10)
    ).ask()
    min_review_count = int(min_review_raw.strip() or 10)

    min_rating_raw = questionary.text(
        "5) Minimum rating (0-5):", default="4.0", validate=_positive_float(4.0)
    ).ask()
    min_rating = float(min_rating_raw.strip() or 4.0)

    max_per_sector_raw = questionary.text(
        "6) Max businesses per sector (controls SerpAPI spend):",
        default="25",
        validate=_positive_int(25),
    ).ask()
    max_per_sector = int(max_per_sector_raw.strip() or 25)

    exclude_group_owned = questionary.confirm(
        "7) Exclude group/corporate-owned businesses via Companies House PSC check?",
        default=True,
    ).ask()

    dry_run = questionary.confirm(
        "8) Dry run? (discovery only, stop before Companies House)",
        default=True,
    ).ask()

    return RunConfig(
        area=area,
        radius=radius,
        sector_slugs=selected_slugs,
        custom_sectors=custom_sectors,
        min_review_count=min_review_count,
        min_rating=min_rating,
        max_per_sector=max_per_sector,
        exclude_group_owned=bool(exclude_group_owned),
        dry_run=bool(dry_run),
    )


def print_cost_estimate(cfg: RunConfig) -> None:
    sector_count = len(cfg.sector_slugs) + len(cfg.custom_sectors)
    max_businesses = sector_count * cfg.max_per_sector

    print("\n--- Cost estimate ---")
    print(f"SerpAPI: up to {sector_count} discovery calls + up to {max_businesses} review calls "
          f"(free tier assumed — check your SerpAPI plan; paid plans bill per search).")
    print("Companies House: free (public API, rate-limited to 600 req/5min).")
    print("---------------------\n")


def confirm_to_proceed(cfg: RunConfig) -> bool:
    label = "Proceed with dry run (discovery only)?" if cfg.dry_run else \
        "Proceed and spend on SerpAPI + Companies House as estimated above?"
    return bool(questionary.confirm(label, default=True).ask())
