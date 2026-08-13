"""CLI entrypoint — `prospector run` (wizard + pipeline), `prospector export`,
plus the Prospector v2 command groups added in Phases 2-5: `discover`,
`reviews`, `site`, `targets`."""
from __future__ import annotations

from pathlib import Path

import click

from prospector import chain_signals
from prospector.config import missing_keys
from prospector.db import get_conn, init_db
from prospector.discovery.places import discover_run, import_csv
from prospector.enrichers.reviews import fetch_and_score as fetch_and_score_reviews
from prospector.enrichers.site import fetch_all as fetch_all_site_signals
from prospector.export import export_run_csv
from prospector.targets import export_targets_csv, list_targets
from prospector.pipeline import print_summary, run_pipeline
from prospector.report import generate_business_report, generate_run_report
from prospector.wizard import confirm_to_proceed, print_cost_estimate, run_wizard


@click.group()
def cli() -> None:
    """prospector — local business prospecting pipeline for Antek Automation."""
    init_db()


@cli.command()
def run() -> None:
    """Run the interactive wizard and execute a prospecting pipeline."""
    missing = missing_keys()
    if missing:
        click.echo(f"Warning: missing .env keys: {', '.join(missing)}. "
                    "Some steps will fail until these are set.", err=True)

    cfg = run_wizard()
    print_cost_estimate(cfg)
    if not confirm_to_proceed(cfg):
        click.echo("Aborted.")
        return

    with get_conn() as conn:
        result = run_pipeline(conn, cfg)

    print_summary(result)
    if cfg.dry_run:
        click.echo("\nDry run finished — no Companies House calls were made, no businesses were written to the DB.")
        click.echo("Re-run and answer 'No' to the dry-run prompt to persist results.")


@cli.command()
@click.option("--run-id", "run_id", type=int, required=True, help="Run id to export.")
@click.option("--format", "fmt", type=click.Choice(["csv"]), default="csv", help="Export format.")
def export(run_id: int, fmt: str) -> None:
    """Export a run's businesses to CSV, sorted by priority then score."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM businesses WHERE run_id = ?", (run_id,)).fetchone()[0]
        if count == 0:
            click.echo(f"No businesses found for run_id={run_id}. Nothing to export.")
            return
        path = export_run_csv(conn, run_id)
    click.echo(f"Exported {count} businesses to {path}")


@cli.command()
@click.option("--run-id", "run_id", type=int, default=None, help="Run id to report on (whole-run snapshot).")
@click.option("--business-id", "business_id", type=int, default=None, help="Single business id (one-page sales snapshot).")
@click.option("--limit", "limit", type=int, default=25, help="Max businesses to include in a run report (default 25, sorted priority then score). Ignored for --business-id.")
def report(run_id: int | None, business_id: int | None, limit: int) -> None:
    """Generate a branded PDF report — either a run-wide top-prospects
    snapshot (--run-id) or a single-business sales-readiness one-pager
    (--business-id). Requires `playwright install chromium` once."""
    if bool(run_id) == bool(business_id):
        raise click.UsageError("Pass exactly one of --run-id or --business-id.")

    with get_conn() as conn:
        if run_id:
            html_path, pdf_path = generate_run_report(conn, run_id, limit=limit)
        else:
            html_path, pdf_path = generate_business_report(conn, business_id)

    size_kb = round(pdf_path.stat().st_size / 1024)
    click.echo(f"Report written to: {html_path}")
    click.echo(f"PDF written to: {pdf_path} ({size_kb} KB)")


@cli.command(name="list-runs")
def list_runs() -> None:
    """List all runs stored in the database."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, area, trade_sectors FROM runs ORDER BY id DESC"
        ).fetchall()
    if not rows:
        click.echo("No runs yet. Use `prospector run` to start one.")
        return
    for row in rows:
        biz_count = None
        with get_conn() as conn:
            biz_count = conn.execute(
                "SELECT COUNT(*) FROM businesses WHERE run_id = ?", (row["id"],)
            ).fetchone()[0]
        click.echo(f"#{row['id']}  {row['created_at']}  area={row['area']!r}  "
                    f"sectors={row['trade_sectors']}  businesses={biz_count}")


@cli.group()
def discover() -> None:
    """Prospector v2 discovery module (Phase 2) — vertical x location Google
    Places search, Companies House enrichment, dedupe against the DB."""


@discover.command(name="run")
@click.option("--vertical", "vertical", required=True, help="A prospector.verticals slug/name, or freeform text.")
@click.option("--location", "location", required=True, help="UK town/city (freeform, or one of prospector.locations.LOCATIONS).")
@click.option("--max-results", "max_results", type=int, default=20, help="Max businesses to discover (default 20).")
@click.option("--no-companies-house", "no_ch", is_flag=True, default=False, help="Skip Companies House enrichment (faster, no CH API calls).")
def discover_run_cmd(vertical: str, location: str, max_results: int, no_ch: bool) -> None:
    """Discover businesses for one vertical/location pair."""
    missing = missing_keys()
    if missing:
        click.echo(f"Warning: missing .env keys: {', '.join(missing)}.", err=True)

    with get_conn() as conn:
        result = discover_run(conn, vertical, location, max_results=max_results, do_ch=not no_ch)

    click.echo(f"Run #{result.run_id}: {result.vertical} in {result.location}")
    click.echo(f"  found={result.found}  deduped_skipped={result.deduped_skipped}  inserted={result.inserted}  chain_flagged={result.chain_flagged}")


@discover.command(name="import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--max-results", "max_results", type=int, default=20, help="Default max businesses per row (default 20; overridable per-row via a max_results CSV column).")
@click.option("--no-companies-house", "no_ch", is_flag=True, default=False, help="Skip Companies House enrichment for all rows.")
def discover_import_cmd(csv_path: Path, max_results: int, no_ch: bool) -> None:
    """Bulk-discover from a CSV of vertical,location pairs (optional max_results column)."""
    with get_conn() as conn:
        results = import_csv(conn, csv_path, max_results=max_results, do_ch=not no_ch)

    total_found = sum(r.found for r in results)
    total_inserted = sum(r.inserted for r in results)
    total_deduped = sum(r.deduped_skipped for r in results)
    total_chain = sum(r.chain_flagged for r in results)
    for r in results:
        click.echo(f"Run #{r.run_id}: {r.vertical} in {r.location} — found={r.found} deduped={r.deduped_skipped} inserted={r.inserted} chain_flagged={r.chain_flagged}")
    click.echo(f"\nTotal: {len(results)} rows, found={total_found}, deduped_skipped={total_deduped}, inserted={total_inserted}, chain_flagged={total_chain}")


@cli.group()
def reviews() -> None:
    """Prospector v2 review profile module (Phase 3) — Google Places
    review-snippet fetch + review_target_score."""


@reviews.command(name="fetch")
@click.option("--run-id", "run_id", type=int, default=None, help="Limit to one discover run.")
@click.option("--business-id", "business_id", type=int, default=None, help="Limit to one business.")
@click.option("--refresh", "refresh", is_flag=True, default=False, help="Re-fetch even businesses already scored.")
def reviews_fetch_cmd(run_id: int | None, business_id: int | None, refresh: bool) -> None:
    """Fetch review profile + score for businesses with a google_place_id."""
    with get_conn() as conn:
        results = fetch_and_score_reviews(conn, run_id=run_id, business_id=business_id, refresh=refresh)

    if not results:
        click.echo("No businesses to fetch (already scored, or none with a google_place_id — run `discover run` first).")
        return
    for r in results:
        listing = "listing found" if r.found_listing else "NO LISTING"
        click.echo(
            f"#{r.business_id} {r.name}: {listing}  rating={r.avg_rating}  reviews={r.review_count}  "
            f"score={r.review_target_score}  weak_gbp={r.weak_gbp}  "
            f"has_negative_recent={r.has_negative_recent}  missed_call_evidence={r.missed_call_evidence}"
        )


@reviews.command(name="list")
@click.option("--min-score", "min_score", type=int, default=0, help="Only show businesses with review_target_score >= this.")
def reviews_list_cmd(min_score: int) -> None:
    """List scored businesses, sorted review_target_score descending."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, vertical, town, rating, review_count, review_target_score, "
            "weak_gbp, missed_call_evidence FROM businesses "
            "WHERE review_target_score IS NOT NULL AND review_target_score >= ? "
            "ORDER BY review_target_score DESC",
            (min_score,),
        ).fetchall()
    if not rows:
        click.echo("No scored businesses match. Run `prospector reviews fetch` first.")
        return
    for row in rows:
        flags = []
        if row["weak_gbp"]:
            flags.append("weak_gbp")
        if row["missed_call_evidence"]:
            flags.append("missed_call_evidence")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        click.echo(
            f"#{row['id']} {row['name']} ({row['vertical']}, {row['town']}) — "
            f"score={row['review_target_score']} rating={row['rating']} reviews={row['review_count']}{flag_str}"
        )


@cli.group()
def site() -> None:
    """Prospector v2 website signal module (Phase 4) — lightweight homepage/
    contact-page fetch for booking/chat-widget and phone-dependency signals."""


@site.command(name="fetch")
@click.option("--run-id", "run_id", type=int, default=None, help="Limit to one discover run.")
@click.option("--business-id", "business_id", type=int, default=None, help="Limit to one business.")
@click.option("--refresh", "refresh", is_flag=True, default=False, help="Re-fetch even businesses already checked.")
@click.option("--limit", "limit", type=int, default=None, help="Cap how many businesses to fetch this invocation (polite default: no cap, but useful for small test batches).")
def site_fetch_cmd(run_id: int | None, business_id: int | None, refresh: bool, limit: int | None) -> None:
    """Fetch website signals (booking/chat widgets, phone-dependency) for businesses with a website."""
    with get_conn() as conn:
        results = fetch_all_site_signals(conn, run_id=run_id, business_id=business_id, refresh=refresh, limit=limit)

    if not results:
        click.echo("No businesses to fetch (already checked, or none with a website — run `discover run` first).")
        return
    for r in results:
        status = f"fetched via {r.fetch_method}" if r.fetched else "UNREACHABLE/NO WEBSITE"
        click.echo(
            f"#{r.business_id} {r.name}: {status}  no_booking={r.no_booking}  no_chat={r.no_chat}  "
            f"phone_dependent={r.phone_dependent}  opportunity_score={r.opportunity_score}"
            + (f"  email={r.email}" if r.email else "")
        )
    methods = [r.fetch_method for r in results if r.fetched]
    if methods:
        counts = {m: methods.count(m) for m in ("httpx", "playwright", "apify")}
        click.echo(
            f"Fetch layer usage: httpx={counts['httpx']}  playwright={counts['playwright']}  "
            f"apify={counts['apify']}  unreachable={len(results) - len(methods)}"
        )


@cli.group()
def chain() -> None:
    """Chain/franchise/corporate exclusion rule (prospector/chain_signals.py)."""


@chain.command(name="rescan")
def chain_rescan_cmd() -> None:
    """Recompute is_chain/chain_reason for every business already in the
    DB, using only already-stored data (no new API calls) — run this once
    after upgrading to backfill businesses discovered before this feature
    existed."""
    with get_conn() as conn:
        flagged = chain_signals.rescan_existing(conn)
    click.echo(f"Rescan complete — {flagged} businesses now flagged is_chain=1.")


@cli.group()
def targets() -> None:
    """Prospector v2 combined targeting (Phase 5) — review_target_score
    (primary) + opportunity_score (tiebreak) sorted list/export."""


@targets.command(name="list")
@click.option("--min-score", "min_score", type=int, default=0, help="Only show businesses with review_target_score >= this.")
@click.option("--run-id", "run_id", type=int, default=None, help="Limit to one discover run.")
@click.option("--include-chains", "include_chains", is_flag=True, default=False, help="Include businesses flagged is_chain=1 (corporate/franchise/chain — excluded by default, see README 'Chain/franchise exclusion').")
def targets_list_cmd(min_score: int, run_id: int | None, include_chains: bool) -> None:
    """List targets sorted review_target_score desc, opportunity_score desc tiebreak.

    Excludes businesses flagged is_chain=1 (corporate/franchise/chain) by
    default — pass --include-chains to see them anyway.
    """
    with get_conn() as conn:
        rows = list_targets(conn, min_score=min_score, run_id=run_id, include_chains=include_chains)
    if not rows:
        click.echo("No targets match. Run `discover run` -> `reviews fetch` -> `site fetch` first.")
        return
    for row in rows:
        chain_tag = " [CHAIN]" if row["is_chain"] else ""
        director = f" | director={row['director_name']}" if row["director_name"] else ""
        click.echo(
            f"#{row['id']} {row['name']} | {row['vertical']} | {row['town']} | {row['phone']} | "
            f"reviews={row['review_count']} rating={row['rating']} | "
            f"review_target_score={row['review_target_score']} opportunity_score={row['opportunity_score']}"
            f"{director}{chain_tag}"
        )


@targets.command(name="export")
@click.argument("csv_path", type=click.Path(path_type=Path), required=False, default=None)
@click.option("--min-score", "min_score", type=int, default=0, help="Only export businesses with review_target_score >= this.")
@click.option("--run-id", "run_id", type=int, default=None, help="Limit to one discover run.")
@click.option("--include-chains", "include_chains", is_flag=True, default=False, help="Include businesses flagged is_chain=1 (corporate/franchise/chain — excluded by default, see README 'Chain/franchise exclusion').")
def targets_export_cmd(csv_path: Path | None, min_score: int, run_id: int | None, include_chains: bool) -> None:
    """Export targets to CSV (default ./exports/targets_<timestamp>.csv).

    Excludes businesses flagged is_chain=1 (corporate/franchise/chain) by
    default — pass --include-chains to include them (e.g. to audit why
    something got flagged, via the chain_reason column).
    """
    with get_conn() as conn:
        path = export_targets_csv(conn, out_path=csv_path, min_score=min_score, run_id=run_id, include_chains=include_chains)
    click.echo(f"Exported targets to {path}")


if __name__ == "__main__":
    cli()
