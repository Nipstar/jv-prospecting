"""Pipeline orchestration — discover, filter, reviews, ownership, score.

Runs per sector, per business. Errors from any single business/sector are
caught and logged so one bad API call doesn't kill the whole run;
retries/backoff live in http.py.

The ad-spend module (Meta/Google ad checks via Apify) was removed in the
"Prospector v2: UK High-Ticket Firms, Review-Based Targeting" rebuild's
Phase 1 — the old targeting model scored businesses by ad spend; the new
model (later phases) targets weak review profiles instead.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from prospector import companies_house_client, places_client, serpapi_client
from prospector.db import (
    create_run,
    insert_business,
    insert_review,
)
from prospector.http import ApiError
from prospector.pain import has_pain_signal
from prospector.scoring import score_business
from prospector.wizard import RunConfig

console = Console()


@dataclass
class RunResult:
    run_id: int
    total_discovered: int = 0
    total_after_filters: int = 0
    priority_counts: dict = field(default_factory=lambda: {"A": 0, "B": 0, "C": 0})


def _discover_businesses(vertical: str, search_term: str, cfg: RunConfig) -> list[dict]:
    """Discover businesses for one sector/area, Google Places first.

    Google Places API is the default discovery source (Andy already pays
    for GOOGLE_PLACES_API_KEY). SerpAPI is kept as an automatic fallback —
    used transparently if Places raises (missing/invalid key, HTTP error,
    etc.) or comes back with 0 results — so discovery never silently stops
    working just because one of the two sources is having a bad day. Either
    source returns the identical dict shape, so nothing downstream of this
    function needs to know which one ran.
    """
    try:
        discovered = places_client.discover_businesses(search_term, cfg.area, cfg.radius, cfg.max_per_sector)
        if discovered:
            return discovered
        console.print(f"[yellow]Google Places returned 0 results for {vertical} — falling back to SerpAPI.[/yellow]")
    except ApiError as exc:
        console.print(f"[yellow]Google Places discovery failed for {vertical}: {exc} — falling back to SerpAPI.[/yellow]")

    return serpapi_client.discover_businesses(search_term, cfg.area, cfg.radius, cfg.max_per_sector)


def _passes_filters(biz: dict, cfg: RunConfig) -> bool:
    if not biz.get("website"):
        return False
    rating = biz.get("rating") or 0
    review_count = biz.get("review_count") or 0
    if rating < cfg.min_rating:
        return False
    if review_count < cfg.min_review_count:
        return False
    return True


def run_pipeline(conn: sqlite3.Connection, cfg: RunConfig) -> RunResult:
    sector_terms = cfg.all_sector_terms
    sector_labels = [label for label, _ in sector_terms]

    run_id = create_run(conn, cfg.area, sector_labels, notes=f"radius={cfg.radius}, dry_run={cfg.dry_run}")
    result = RunResult(run_id=run_id)

    # --- Step 1-3: per sector discovery, filtering, reviews ----------------
    surviving: list[dict] = []  # each carries vertical + reviews
    for vertical, search_term in sector_terms:
        console.print(f"[bold cyan]Discovering[/bold cyan] {vertical} near {cfg.area} ({cfg.radius})...")
        try:
            discovered = _discover_businesses(vertical, search_term, cfg)
        except ApiError as exc:
            console.print(f"[red]Discovery failed for {vertical} (Places and SerpAPI both failed): {exc}[/red]")
            continue

        result.total_discovered += len(discovered)
        console.print(f"  found {len(discovered)}, filtering (rating>={cfg.min_rating}, reviews>={cfg.min_review_count}, has website)...")

        for biz in discovered:
            if not _passes_filters(biz, cfg):
                continue
            biz["vertical"] = vertical

            reviews: list[dict] = []
            place_id = biz.get("google_place_id")
            if place_id and not cfg.dry_run:
                try:
                    reviews = serpapi_client.fetch_reviews(place_id, limit=20)
                except ApiError as exc:
                    console.print(f"[yellow]Reviews fetch failed for {biz.get('name')}: {exc}[/yellow]")
            for r in reviews:
                r["pain_flag"] = has_pain_signal(r.get("text"))
            biz["_reviews"] = reviews
            surviving.append(biz)

    result.total_after_filters = len(surviving)

    if cfg.dry_run:
        # Dry run stops here — discovery (+ optional reviews) only, no
        # Companies House calls, no DB writes for businesses.
        console.print("[bold yellow]Dry run complete[/bold yellow] — stopping before Companies House.")
        return result

    # --- Step 4: ownership (Companies House) --------------------------------
    if cfg.exclude_group_owned:
        console.print("[bold cyan]Checking ownership via Companies House...[/bold cyan]")
        filtered = []
        for biz in surviving:
            try:
                ownership = companies_house_client.check_ownership(biz["name"])
            except ApiError as exc:
                console.print(f"[yellow]Companies House lookup failed for {biz.get('name')}: {exc}[/yellow]")
                ownership = {"company_number": None, "director_name": None, "is_group_owned": False}
            biz.update({
                "companies_house_number": ownership.get("company_number"),
                "director_name": ownership.get("director_name"),
                "is_group_owned": bool(ownership.get("is_group_owned")),
            })
            if ownership.get("is_group_owned"):
                continue
            filtered.append(biz)
        surviving = filtered
        result.total_after_filters = len(surviving)
    else:
        for biz in surviving:
            biz.setdefault("companies_house_number", None)
            biz.setdefault("director_name", None)
            biz.setdefault("is_group_owned", False)

    result.total_after_filters = len(surviving)

    # --- Step 5: score, write to DB ------------------------------------------
    for biz in surviving:
        has_pain = any(r.get("pain_flag") for r in biz.get("_reviews", []))
        is_independent = not biz.get("is_group_owned", False)
        priority, score = score_business(
            has_pain_flag=has_pain,
            is_independent=is_independent,
            review_count=biz.get("review_count"),
        )
        biz["priority"] = priority
        biz["priority_score"] = score
        result.priority_counts[priority] += 1

        business_id = insert_business(conn, run_id, biz)
        for r in biz.get("_reviews", []):
            insert_review(conn, business_id, r)

    return result


def print_summary(result: RunResult) -> None:
    table = Table(title=f"Run #{result.run_id} summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total discovered", str(result.total_discovered))
    table.add_row("Total after filters", str(result.total_after_filters))
    table.add_row("Priority A", str(result.priority_counts.get("A", 0)))
    table.add_row("Priority B", str(result.priority_counts.get("B", 0)))
    table.add_row("Priority C", str(result.priority_counts.get("C", 0)))
    console.print(table)
