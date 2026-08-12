"""CLI entrypoint — `prospector run` (wizard + pipeline), `prospector export`,
plus the Prospector v2 command groups added in Phases 2-5: `discover`,
`reviews`, `site`, `targets`."""
from __future__ import annotations

from pathlib import Path

import click

from prospector.config import missing_keys
from prospector.db import get_conn, init_db
from prospector.discovery.places import discover_run, import_csv
from prospector.export import export_run_csv
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
    click.echo(f"  found={result.found}  deduped_skipped={result.deduped_skipped}  inserted={result.inserted}")


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
    for r in results:
        click.echo(f"Run #{r.run_id}: {r.vertical} in {r.location} — found={r.found} deduped={r.deduped_skipped} inserted={r.inserted}")
    click.echo(f"\nTotal: {len(results)} rows, found={total_found}, deduped_skipped={total_deduped}, inserted={total_inserted}")


if __name__ == "__main__":
    cli()
