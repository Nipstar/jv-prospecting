"""CLI entrypoint — `prospector run` (wizard + pipeline), `prospector export`,
plus the Prospector v2 command groups added in Phases 2-5: `discover`,
`reviews`, `site`, `targets`."""
from __future__ import annotations

from pathlib import Path

import click

from prospector import chain_signals
from prospector.config import missing_keys
from prospector.db import get_conn, init_db
from prospector.discovery.places import discover_run, import_csv, resolve_sources
from prospector.enrichers.crosscheck import crosscheck_organic
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
@click.option("--limit", "limit", type=int, default=25, help="Max businesses to include in a run report (default 25, sorted review_target_score desc, opportunity_score desc tiebreak). Ignored for --business-id.")
@click.option("--include-chains", "include_chains", is_flag=True, default=False, help="Include businesses flagged is_chain=1 in a run report (excluded by default, same as `targets list`/`export` — see README 'Chain/franchise exclusion'). Ignored for --business-id.")
@click.option("--deploy", "do_deploy", is_flag=True, default=False, help="Also push a live HTML version of this run's report (+ the full reports index) to Cloudflare Pages. Run-report only, ignored for --business-id.")
def report(run_id: int | None, business_id: int | None, limit: int, include_chains: bool, do_deploy: bool) -> None:
    """Generate a branded PDF report — either a run-wide top-targets
    snapshot (--run-id) or a single-business sales-readiness one-pager
    (--business-id). Requires `playwright install chromium` once."""
    if bool(run_id) == bool(business_id):
        raise click.UsageError("Pass exactly one of --run-id or --business-id.")

    with get_conn() as conn:
        if run_id:
            html_path, pdf_path = generate_run_report(conn, run_id, limit=limit, include_chains=include_chains)
        else:
            html_path, pdf_path = generate_business_report(conn, business_id)

    size_kb = round(pdf_path.stat().st_size / 1024)
    click.echo(f"Report written to: {html_path}")
    click.echo(f"PDF written to: {pdf_path} ({size_kb} KB)")

    if do_deploy:
        if not run_id:
            raise click.UsageError("--deploy only applies to --run-id reports.")
        from prospector.deploy import deploy_run
        with get_conn() as conn:
            result = deploy_run(conn, run_id, force_regenerate=True)
        click.echo(f"Deployed to Cloudflare Pages: {result['run_url']}")
        click.echo(f"Reports index: {result['index_url']}")


@cli.command()
@click.option("--run-id", "run_id", type=int, required=True, help="Run id to deploy a live report for.")
@click.option("--force", "force_regenerate", is_flag=True, default=False, help="Re-render this run's PDF/HTML even if reports/runs/ already has one.")
def deploy(run_id: int, force_regenerate: bool) -> None:
    """Build (if needed) and deploy this run's report — plus every other
    run's already-generated report and the master index — to Cloudflare
    Pages. Equivalent to `prospector report --run-id N --deploy` but
    doesn't re-render the PDF unless --force is passed or reports/runs/
    doesn't have one yet."""
    from prospector.deploy import deploy_run
    with get_conn() as conn:
        result = deploy_run(conn, run_id, force_regenerate=force_regenerate)
    click.echo(f"Deployed to Cloudflare Pages: {result['run_url']}")
    click.echo(f"Reports index: {result['index_url']}")
    click.echo(f"Runs on site: {result['run_ids']}")


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
    """Prospector v2 discovery module (Phase 2, extended) — vertical x
    location search across Google Places, Yell.com, SerpAPI organic
    search, and Checkatrade.com (see --source), Companies House
    enrichment, dedupe against the DB (including across sources — see
    README 'Multi-source discovery')."""


@discover.command(name="run")
@click.option("--vertical", "vertical", required=True, help="A prospector.verticals slug/name, or freeform text.")
@click.option("--location", "location", required=True, help="UK town/city (freeform, or one of prospector.locations.LOCATIONS).")
@click.option("--max-results", "max_results", type=int, default=20, help="Max businesses to discover PER SOURCE (default 20).")
@click.option("--no-companies-house", "no_ch", is_flag=True, default=False, help="Skip Companies House enrichment (faster, no CH API calls).")
@click.option("--source", "source", default="all", help="Discovery source(s): places, yell, organic, checkatrade, or a comma-list (e.g. places,checkatrade). Default 'all' runs all four additively and dedupes across them.")
def discover_run_cmd(vertical: str, location: str, max_results: int, no_ch: bool, source: str) -> None:
    """Discover businesses for one vertical/location pair."""
    missing = missing_keys()
    if missing:
        click.echo(f"Warning: missing .env keys: {', '.join(missing)}.", err=True)

    sources = resolve_sources(source)

    with get_conn() as conn:
        result = discover_run(conn, vertical, location, max_results=max_results, do_ch=not no_ch, sources=sources)

    click.echo(f"Run #{result.run_id}: {result.vertical} in {result.location}  (sources={','.join(result.sources_run)})")
    click.echo(f"  found={result.found}  deduped_skipped={result.deduped_skipped}  inserted={result.inserted}  chain_flagged={result.chain_flagged}")
    if result.found_by_source:
        breakdown = "  ".join(f"{s}: found={result.found_by_source.get(s, 0)} inserted={result.inserted_by_source.get(s, 0)}" for s in result.sources_run)
        click.echo(f"  by source — {breakdown}")
    if result.source_errors:
        for s, err in result.source_errors.items():
            click.echo(f"  [{s}] error: {err}", err=True)


@discover.command(name="import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--max-results", "max_results", type=int, default=20, help="Default max businesses per row per source (default 20; overridable per-row via a max_results CSV column).")
@click.option("--no-companies-house", "no_ch", is_flag=True, default=False, help="Skip Companies House enrichment for all rows.")
@click.option("--source", "source", default="all", help="Discovery source(s): places, yell, organic, checkatrade, or a comma-list. Default 'all'.")
def discover_import_cmd(csv_path: Path, max_results: int, no_ch: bool, source: str) -> None:
    """Bulk-discover from a CSV of vertical,location pairs (optional max_results column)."""
    sources = resolve_sources(source)
    with get_conn() as conn:
        results = import_csv(conn, csv_path, max_results=max_results, do_ch=not no_ch, sources=sources)

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


_SITE_FETCH_DEFAULT_BATCH_SIZE = 10
# Each business can trigger up to 2 URL fetches (homepage + contact page),
# each of which may escalate through httpx -> a fresh Playwright Chromium
# launch -> an Apify actor call. Processing an unbounded batch in one
# `fetch_all` call was found to be unreliable in practice — a run of ~28
# businesses got killed (exit 137) partway through with zero progress
# committed, because a single interrupted Python process loses everything
# that hadn't reached its per-row commit yet even though update_business_
# fields() commits per business (the interrupt can land mid-network-call,
# before that business's row is ever written). Auto-chunking into small
# batches, each a *separate* `fetch_all` call with its own `with get_conn()`
# transaction, bounds the blast radius of any one crash to at most one
# batch's worth of businesses rather than the whole run, and turns "resume
# after a crash" into "just re-run the same command" since already-checked
# businesses (site_checked_at IS NOT NULL) are automatically skipped on the
# next batch's query. This is the default behavior now — --limit no longer
# needs to be set manually for reliability, only to intentionally do less.

@site.command(name="fetch")
@click.option("--run-id", "run_id", type=int, default=None, help="Limit to one discover run.")
@click.option("--business-id", "business_id", type=int, default=None, help="Limit to one business.")
@click.option("--refresh", "refresh", is_flag=True, default=False, help="Re-fetch even businesses already checked.")
@click.option("--limit", "limit", type=int, default=None, help="Cap how many businesses to fetch in TOTAL this invocation (across all batches). Default: no total cap, but still auto-chunked into small batches — see --batch-size.")
@click.option("--batch-size", "batch_size", type=int, default=_SITE_FETCH_DEFAULT_BATCH_SIZE, help=f"Businesses processed per internal batch/transaction (default {_SITE_FETCH_DEFAULT_BATCH_SIZE}) — bounds how much work a single crash can lose. Lower this further if crashes persist; raise it (or pass a huge number) to opt back into one big unbatched pass.")
def site_fetch_cmd(run_id: int | None, business_id: int | None, refresh: bool, limit: int | None, batch_size: int) -> None:
    """Fetch website signals (booking/chat widgets, phone-dependency) for businesses with a website.

    Auto-chunks into small batches (see --batch-size) for reliability —
    each batch is its own DB transaction, so a crash mid-run only loses
    that one batch's progress, and simply re-running the command resumes
    from where it left off (already-checked businesses are skipped)."""
    total_processed = 0
    total_target = limit  # None = no cap, keep batching until a batch comes back empty
    all_results = []
    batch_num = 0
    while True:
        this_batch_limit = batch_size
        if total_target is not None:
            remaining = total_target - total_processed
            if remaining <= 0:
                break
            this_batch_limit = min(batch_size, remaining)

        batch_num += 1
        with get_conn() as conn:
            batch_results = fetch_all_site_signals(
                conn, run_id=run_id, business_id=business_id, refresh=refresh, limit=this_batch_limit
            )

        if not batch_results:
            break

        all_results.extend(batch_results)
        total_processed += len(batch_results)
        if len(batch_results) == this_batch_limit and (total_target is None or total_processed < total_target):
            click.echo(f"  [site] batch {batch_num} done ({len(batch_results)} businesses, {total_processed} so far) — continuing...")

        if business_id is not None:
            break  # single-business mode never needs a second batch

    results = all_results
    if not results:
        click.echo("No businesses to fetch (already checked — run `discover run` first).")
        return
    for r in results:
        if r.fetched:
            status = f"fetched via {r.fetch_method}"
        elif r.no_website:
            status = "NO WEBSITE"
        else:
            status = "UNREACHABLE"
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
def crosscheck() -> None:
    """Cross-check/validation module (prospector/enrichers/crosscheck.py)
    — for organic-sourced businesses, a targeted GBP name+location lookup
    plus a read of the (already source-agnostic) Companies House
    enrichment, to gauge how much of SerpAPI organic search's output is a
    genuinely findable/real local business vs. unvalidated."""


@crosscheck.command(name="organic")
@click.option("--run-id", "run_id", type=int, default=None, help="Limit to one discover run.")
@click.option("--business-id", "business_id", type=int, default=None, help="Limit to one business.")
@click.option("--refresh", "refresh", is_flag=True, default=False, help="Re-check even businesses already cross-checked.")
def crosscheck_organic_cmd(run_id: int | None, business_id: int | None, refresh: bool) -> None:
    """Cross-check organic-sourced businesses against GBP + Companies House."""
    with get_conn() as conn:
        results = crosscheck_organic(conn, run_id=run_id, business_id=business_id, refresh=refresh)

    if not results:
        click.echo("No organic-sourced businesses to cross-check (already checked, or none found — run `discover run` first).")
        return
    validated = sum(1 for r in results if r.organic_validated)
    no_gbp = sum(1 for r in results if r.gbp_status == "no_gbp_found")
    gbp_found = sum(1 for r in results if r.gbp_status == "validated_gbp")
    for r in results:
        click.echo(f"#{r.business_id} {r.name}: {r.gbp_status}  organic_validated={int(r.organic_validated)}")
    click.echo(f"\nTotal: {len(results)}  gbp_matches={gbp_found}  no_gbp_found={no_gbp}  organic_validated={validated}")


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
