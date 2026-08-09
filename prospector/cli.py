"""CLI entrypoint — `prospector run` (wizard + pipeline), `prospector export`."""
from __future__ import annotations

import click

from prospector.config import missing_keys
from prospector.db import get_conn, init_db
from prospector.export import export_run_csv
from prospector.pipeline import collect_pending_apify_runs, print_summary, run_pipeline
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
        click.echo("\nDry run finished — no Apify calls were made, no businesses were written to the DB.")
        click.echo("Re-run and answer 'No' to the dry-run prompt to spend on Apify and persist results.")


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
@click.option("--run-id", "run_id", type=int, required=True, help="Run id to collect pending Apify results for.")
def collect(run_id: int) -> None:
    """Poll any Apify runs still pending from `prospector run` (the async
    fallback for runs that didn't finish inside the 60s sync-poll window)
    and, for any that have now finished, update the stored businesses'
    fb_ads_active / google_ads_active and re-score them."""
    with get_conn() as conn:
        resolved, still_pending = collect_pending_apify_runs(conn, run_id)
    if resolved == 0 and still_pending == 0:
        click.echo(f"No pending Apify runs for run_id={run_id}.")
        return
    click.echo(f"Resolved {resolved} pending Apify run entr{'y' if resolved == 1 else 'ies'}; "
               f"{still_pending} still running (re-run `prospector collect --run-id {run_id}` later).")


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


if __name__ == "__main__":
    cli()
