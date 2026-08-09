"""Pipeline orchestration — discover, filter, reviews, ownership, ads, score.

Runs per sector, per business, per the spec's 9-step pipeline logic. Errors
from any single business/sector are caught and logged so one bad API call
doesn't kill the whole run; retries/backoff live in clients/http.py.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from prospector import apify_client, companies_house_client, serpapi_client
from prospector.db import (
    add_pending_apify_run,
    create_run,
    get_business_for_rescore,
    get_pending_apify_runs,
    has_pain_flagged_review,
    insert_business,
    insert_review,
    set_pending_apify_runs,
    update_business_ad_fields,
    update_business_priority,
)
from prospector.http import ApiError
from prospector.pain import has_pain_signal
from prospector.scoring import score_business
from prospector.wizard import RunConfig, estimate_apify_cost

console = Console()


@dataclass
class RunResult:
    run_id: int
    total_discovered: int = 0
    total_after_filters: int = 0
    priority_counts: dict = field(default_factory=lambda: {"A": 0, "B": 0, "C": 0})
    apify_spend_estimate: float = 0.0


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


def _ad_qualifies(fb_active: bool | None, google_active: bool | None, rule: str) -> bool:
    """Apply the wizard's ad-qualification rule, NULL-aware.

    NULL (unknown — Apify failed/timed out/came back empty, or a run is
    still pending async collection) is deliberately NOT treated as "not
    active" here: for the "or" rule we don't want to silently drop a
    business whose ad status simply isn't known yet, since a pending Apify
    run may resolve to True once `prospector collect` picks it up. Only a
    channel that's *confirmed* False (0) counts against qualification.
    "and" is stricter — it requires both channels confirmed True, so an
    unknown channel fails it (we're not confident enough to claim the
    business qualifies on both).
    """
    if rule == "none":
        return True
    if rule == "and":
        return fb_active is True and google_active is True
    # rule == "or" (default): qualifies unless both channels are
    # *confirmed* not-active. Unknown (None) on either side keeps the
    # business in, pending later collection.
    if fb_active is True or google_active is True:
        return True
    return fb_active is None or google_active is None


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
            discovered = serpapi_client.discover_businesses(search_term, cfg.area, cfg.radius, cfg.max_per_sector)
        except ApiError as exc:
            console.print(f"[red]SerpAPI discovery failed for {vertical}: {exc}[/red]")
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
        # Companies House / Apify calls, no DB writes for businesses.
        sector_count = len(sector_terms)
        result.apify_spend_estimate = estimate_apify_cost(sector_count, cfg.max_per_sector)
        console.print(f"[bold yellow]Dry run complete[/bold yellow] — stopping before Companies House / Apify.")
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

    # --- Step 5: Meta ad spend (per business) -------------------------------
    # A single actor-run failure (or an empty result — see apify_client's
    # NULL-vs-0 policy) is logged and the business is left with
    # fb_ads_active = None (unknown); it never aborts the run. A run still
    # going after the 60s sync-poll window is recorded on `biz["_pending"]`
    # so it can be attached to the DB row (once we have a business_id,
    # after insert) for later collection via `prospector collect`.
    console.print("[bold cyan]Checking Meta ad spend...[/bold cyan]")
    for biz in surviving:
        biz.setdefault("_pending", [])
        try:
            fb = apify_client.check_facebook_ads(biz["name"])
        except ApiError as exc:
            console.print(f"[yellow]Facebook ads check failed for {biz.get('name')}: {exc}[/yellow]")
            fb = {"fb_ads_active": None, "fb_ads_creative_count": None, "fb_ads_earliest_seen": None,
                  "_apify_status": "error", "_apify_run_id": None}
        if fb.get("_apify_status") == "pending" and fb.get("_apify_run_id"):
            biz["_pending"].append({"kind": "fb", "apify_run_id": fb["_apify_run_id"]})
        biz.update({k: v for k, v in fb.items() if not k.startswith("_apify_")})

    # --- Step 6: Google ad spend (batched by domain) ------------------------
    console.print("[bold cyan]Checking Google ad spend (batched)...[/bold cyan]")
    domains = [b["domain"] for b in surviving if b.get("domain")]
    try:
        google_ads_by_domain = apify_client.check_google_ads_batch(domains)
    except ApiError as exc:
        console.print(f"[yellow]Google ads batch check failed: {exc}[/yellow]")
        google_ads_by_domain = {
            d: {"google_ads_active": None, "google_ads_creative_count": None,
                "google_ads_days_active": None, "google_ads_advertiser_name": None,
                "_apify_status": "error", "_apify_run_id": None}
            for d in domains
        }
    for biz in surviving:
        entry = google_ads_by_domain.get(biz.get("domain"), {})
        if entry.get("_apify_status") == "pending" and entry.get("_apify_run_id"):
            biz.setdefault("_pending", [])
            biz["_pending"].append({
                "kind": "google", "apify_run_id": entry["_apify_run_id"], "domain": biz.get("domain"),
            })
        biz.update({
            "google_ads_active": entry.get("google_ads_active"),
            "google_ads_creative_count": entry.get("google_ads_creative_count"),
            "google_ads_days_active": entry.get("google_ads_days_active"),
            "google_ads_advertiser_name": entry.get("google_ads_advertiser_name"),
        })

    # --- Step 7: apply ad qualification rule --------------------------------
    # NULL (unknown) is deliberately not treated as "not active" here — see
    # _ad_qualifies' docstring. Businesses with a pending Apify run mostly
    # survive the default "or" rule so `prospector collect` can still
    # reconcile them later.
    qualified = [
        b for b in surviving
        if _ad_qualifies(b.get("fb_ads_active"), b.get("google_ads_active"), cfg.ad_qualification)
    ]
    result.total_after_filters = len(qualified)

    # --- Step 8-9: score, write to DB ---------------------------------------
    pending_this_run: list[dict] = []
    for biz in qualified:
        has_pain = any(r.get("pain_flag") for r in biz.get("_reviews", []))
        is_independent = not biz.get("is_group_owned", False)
        priority, score = score_business(
            fb_ads_active=biz.get("fb_ads_active"),
            google_ads_active=biz.get("google_ads_active"),
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

        for pending in biz.get("_pending", []):
            pending_this_run.append({**pending, "business_id": business_id, "name": biz.get("name")})

    if pending_this_run:
        for entry in pending_this_run:
            add_pending_apify_run(conn, run_id, entry)
        console.print(
            f"[yellow]{len(pending_this_run)} Apify run(s) were still going after the 60s sync-poll "
            f"window — run `prospector collect --run-id {run_id}` later to fetch their results.[/yellow]"
        )

    sector_count = len(sector_terms)
    result.apify_spend_estimate = estimate_apify_cost(sector_count, cfg.max_per_sector)

    return result


def _rescore_business(conn: sqlite3.Connection, business_id: int) -> None:
    row = get_business_for_rescore(conn, business_id)
    if not row:
        return
    has_pain = has_pain_flagged_review(conn, business_id)
    priority, score = score_business(
        fb_ads_active=row["fb_ads_active"],
        google_ads_active=row["google_ads_active"],
        has_pain_flag=has_pain,
        is_independent=not bool(row["is_group_owned"]),
        review_count=row["review_count"],
    )
    update_business_priority(conn, business_id, priority, score)


def collect_pending_apify_runs(conn: sqlite3.Connection, run_id: int) -> tuple[int, int]:
    """Poll every Apify run stashed against `run_id` (async fallback from
    the 60s sync-poll window, see apify_client.run_actor). For each one
    that has now finished, apply the same NULL-vs-0 policy used at
    pipeline time, update the affected businesses.fb_ads_active /
    google_ads_active, and re-score them. Runs still in progress are left
    in place for the next `prospector collect` call.

    Returns (resolved_count, still_pending_count).
    """
    pending = get_pending_apify_runs(conn, run_id)
    if not pending:
        return 0, 0

    by_apify_run: dict[str, list[dict]] = {}
    for entry in pending:
        by_apify_run.setdefault(entry["apify_run_id"], []).append(entry)

    resolved = 0
    remaining: list[dict] = []

    for apify_run_id, entries in by_apify_run.items():
        try:
            run = apify_client.get_run_status(apify_run_id)
        except ApiError as exc:
            console.print(f"[yellow]Could not check Apify run {apify_run_id}: {exc}[/yellow]")
            remaining.extend(entries)
            continue

        status = run.get("status")
        if status in apify_client.RUNNING_STATUSES:
            remaining.extend(entries)
            continue

        if status != "SUCCEEDED":
            console.print(
                f"[yellow]Apify run {apify_run_id} ended with status={status} — leaving affected "
                f"business(es) as NULL (unknown).[/yellow]"
            )
            resolved += len(entries)
            continue

        dataset_id = run.get("defaultDatasetId")
        items = apify_client.get_dataset_items(dataset_id) if dataset_id else []
        kind = entries[0]["kind"]

        if not items:
            console.print(
                f"[yellow]Apify run {apify_run_id} ({kind}) succeeded with 0 items — leaving affected "
                f"business(es) as NULL (unknown), not a confirmed 0.[/yellow]"
            )
            resolved += len(entries)
            continue

        if kind == "fb":
            creative_count = len(items)
            earliest = None
            for item in items:
                started = item.get("startDateFormatted") or item.get("start_date") or item.get("adDeliveryStartTime")
                if started and (earliest is None or started < earliest):
                    earliest = started
            for entry in entries:
                update_business_ad_fields(conn, entry["business_id"], {
                    "fb_ads_active": True,
                    "fb_ads_creative_count": creative_count,
                    "fb_ads_earliest_seen": earliest,
                })
                _rescore_business(conn, entry["business_id"])
        else:  # "google" — batched, resolve every domain in this run at once
            domains = [e["domain"] for e in entries if e.get("domain")]
            by_domain = apify_client.summarize_google_items(items, domains)
            for entry in entries:
                data = by_domain.get(entry.get("domain")) or {
                    "google_ads_active": False, "google_ads_creative_count": 0,
                    "google_ads_days_active": None, "google_ads_advertiser_name": None,
                }
                update_business_ad_fields(conn, entry["business_id"], data)
                _rescore_business(conn, entry["business_id"])

        resolved += len(entries)

    set_pending_apify_runs(conn, run_id, remaining)
    return resolved, len(remaining)


def print_summary(result: RunResult) -> None:
    table = Table(title=f"Run #{result.run_id} summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total discovered", str(result.total_discovered))
    table.add_row("Total after filters", str(result.total_after_filters))
    table.add_row("Priority A", str(result.priority_counts.get("A", 0)))
    table.add_row("Priority B", str(result.priority_counts.get("B", 0)))
    table.add_row("Priority C", str(result.priority_counts.get("C", 0)))
    table.add_row("Apify spend estimate", f"${result.apify_spend_estimate:.2f}")
    console.print(table)
