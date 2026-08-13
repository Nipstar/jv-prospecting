"""Cloudflare Pages deploy — `prospector deploy --run-id N` (and
`prospector report --run-id N --deploy`).

Andy wants, "as the standard output at the end of every run": a live,
branded HTML report per run (same look as the PDF, in a browser, with
download links to the CSV+PDF), plus a master index of every run ever
generated, on the same Cloudflare Pages site. This module builds that
static site and pushes it with `wrangler pages deploy`.

Pattern reused from geo-prospecting's existing Pages deploy
(claim-site/README.md, `wrangler pages deploy claim-site/dist --project-name
antek-claim`) — same CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID from the
shared .env, same non-interactive `npx wrangler` invocation. Project name is
`jv-prospecting-reports` (no existing Pages project for prospector at the
time this was built — see `wrangler pages project list`).

Site layout (rebuilt fresh on every deploy, from files already tracked in
reports/runs/ + exports/runs/ plus whichever run this invocation is for):

    /index.html                  master index of every run
    /runs/{run_id}/index.html    live HTML report for that run
    /runs/{run_id}/*.pdf         same PDF as reports/runs/
    /runs/{run_id}/*.csv         same CSV as exports/runs/

PDF/CSV are copied alongside the HTML so download links are same-origin —
simpler and more reliable than linking out to GitHub raw URLs.

Every deploy re-uploads the *whole* site (Cloudflare Pages has no
incremental/partial deploy), so the dist/ directory is rebuilt from
scratch each time by scanning reports/runs/ for every run that already has
a PDF, not just the run being deployed right now — that is what keeps the
index and every other run's page live and current, not just the one
Andy just ran.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from prospector.config import ROOT, REPORTS_DIR, EXPORTS_DIR
from prospector.export import export_run_csv
from prospector.report import fetch_run_data, render_run_report, html_to_pdf, _safe_slug

PAGES_PROJECT = "jv-prospecting-reports"
DIST_DIR = ROOT / ".pages_dist"

REPORTS_RUNS_DIR = REPORTS_DIR / "runs"
EXPORTS_RUNS_DIR = EXPORTS_DIR / "runs"

_RUN_PDF_RE = re.compile(r"^PROSPECTOR-RUN-(\d+)-.*\.pdf$")
_RUN_CSV_RE = re.compile(r"^run_(\d+)_\d{8}T\d{6}Z\.csv$")


def _existing_pdf(run_id: int) -> Path | None:
    for p in sorted(REPORTS_RUNS_DIR.glob(f"PROSPECTOR-RUN-{run_id}-*.pdf")):
        return p
    return None


def _existing_csv(run_id: int) -> Path | None:
    # Prefer the newest export for this run (filenames are UTC-timestamped).
    matches = sorted(EXPORTS_RUNS_DIR.glob(f"run_{run_id}_*.csv"))
    return matches[-1] if matches else None


def ensure_run_artifacts(conn, run_id: int, force_regenerate: bool = False) -> tuple[Path, Path]:
    """Return (pdf_path, csv_path) for a run, generating + copying them into
    the canonical git-tracked reports/runs/ + exports/runs/ locations if
    they don't already exist (or force_regenerate=True). Report generation
    itself (HTML->PDF via Playwright) is unchanged from `prospector
    report`; this just makes sure the result lands in reports/runs/ instead
    of the flat reports/ dir, matching how runs 2-8/11 were committed."""
    REPORTS_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_RUNS_DIR.mkdir(parents=True, exist_ok=True)

    pdf_path = None if force_regenerate else _existing_pdf(run_id)
    if pdf_path is None:
        from prospector.report import generate_run_report
        html_src, pdf_src = generate_run_report(conn, run_id)
        pdf_path = REPORTS_RUNS_DIR / pdf_src.name
        shutil.copy2(pdf_src, pdf_path)
        shutil.copy2(html_src, REPORTS_RUNS_DIR / html_src.name)

    csv_path = None if force_regenerate else _existing_csv(run_id)
    if csv_path is None:
        csv_src = export_run_csv(conn, run_id)
        csv_path = EXPORTS_RUNS_DIR / csv_src.name
        shutil.copy2(csv_src, csv_path)

    return pdf_path, csv_path


def discover_deployable_run_ids(conn) -> list[int]:
    """Every run_id that already has a PDF report checked into
    reports/runs/ — these are the runs eligible for a live page without
    re-running the (cheap but non-instant) Playwright render."""
    ids = set()
    if REPORTS_RUNS_DIR.exists():
        for p in REPORTS_RUNS_DIR.glob("PROSPECTOR-RUN-*.pdf"):
            m = _RUN_PDF_RE.match(p.name)
            if m:
                ids.add(int(m.group(1)))
    # only keep run_ids that still exist in the DB
    existing = {r["id"] for r in conn.execute("SELECT id FROM runs")}
    return sorted(ids & existing)


def _run_meta(conn, run_id: int) -> dict:
    run = conn.execute(
        "SELECT id, created_at, area, trade_sectors FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    # Total businesses captured in the run — deliberately not filtered to
    # review_target_score IS NOT NULL like targets.py, because some older
    # runs' businesses no longer carry v2 scores in the current DB (rescored
    # schema/data has moved on since) but the run itself is still real and
    # belongs in the index. See build_run_page's static-HTML fallback for
    # the same reasoning applied to the per-run page.
    biz_count = conn.execute(
        "SELECT COUNT(*) FROM businesses WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    try:
        sectors = json.loads(run["trade_sectors"] or "[]")
    except Exception:
        sectors = []
    return {
        "id": run["id"],
        "created_at": run["created_at"],
        "area": run["area"],
        "sectors": sectors,
        "target_count": biz_count,
    }


_STATIC_DOWNLOAD_CSS = """\
<style>
.download-bar{display:flex;gap:12px;flex-wrap:wrap;padding:18px 48px;background:#2C2C2C;border-bottom:3px solid #000}
.download-btn{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#2C2C2C;background:#CD5C3C;border:2px solid #000;padding:10px 18px;text-decoration:none;display:inline-flex;align-items:center;gap:8px}
.download-btn:hover{background:#E8DCC8}
.download-btn.secondary{background:#E8DCC8;color:#2C2C2C}
.index-link-bar{padding:10px 48px;background:#FAF8F5;border-bottom:3px solid #000;font-family:'JetBrains Mono',monospace;font-size:11px}
.index-link-bar a{color:#CD5C3C;text-decoration:none;font-weight:700}
.index-link-bar a:hover{text-decoration:underline}
</style>
</head>"""


def _patch_static_html(html_text: str, pdf_name: str, csv_name: str, index_href: str) -> str:
    """Inject a download bar + back-to-index link into an already-generated
    (static, pre-existing) run report HTML file, for runs whose businesses
    no longer carry v2 scores in the current DB (see _run_meta) so
    fetch_run_data can't re-render them live. Keeps the exact same brand
    template/design — this only adds two small bars, string-injected
    rather than duplicating the whole render pipeline for a one-off case."""
    html_text = html_text.replace("</head>", _STATIC_DOWNLOAD_CSS, 1)
    bars = (
        f'<div class="index-link-bar"><a href="{escape(index_href)}">&larr; All Prospector reports</a></div>'
        f'<div class="download-bar">'
        f'<a class="download-btn" href="{escape(pdf_name)}" download>&#8595; Download PDF</a>'
        f'<a class="download-btn secondary" href="{escape(csv_name)}" download>&#8595; Download CSV</a>'
        f'</div>'
    )
    html_text = html_text.replace("</header>", "</header>\n" + bars, 1)
    return html_text


def build_run_page(conn, run_id: int, dist_dir: Path, force_regenerate: bool = False) -> dict:
    """Build dist_dir/runs/{run_id}/ (index.html + co-located PDF/CSV).

    Tries a live re-render from the DB first (freshest data, matches the
    PDF-source template exactly via render_run_report). Falls back to
    patching the existing static HTML already sitting in reports/runs/ for
    runs whose businesses don't currently carry review_target_score (older
    runs, predating a rescoring pass) — fetch_run_data raises SystemExit
    for those, and re-rendering isn't possible without re-scoring, which is
    out of scope here; the already-generated report is still valid content."""
    pdf_path, csv_path = ensure_run_artifacts(conn, run_id, force_regenerate=force_regenerate)

    run_dir = dist_dir / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, run_dir / pdf_path.name)
    shutil.copy2(csv_path, run_dir / csv_path.name)

    try:
        data = fetch_run_data(conn, run_id, limit=25)
        html = render_run_report(
            data,
            download_links={"pdf": pdf_path.name, "csv": csv_path.name},
            index_href="../../index.html",
        )
    except SystemExit:
        html_src = pdf_path.with_suffix(".html")
        if not html_src.exists():
            raise
        html = _patch_static_html(
            html_src.read_text(encoding="utf-8"), pdf_path.name, csv_path.name, "../../index.html"
        )

    (run_dir / "index.html").write_text(html, encoding="utf-8")

    meta = _run_meta(conn, run_id)
    meta["pdf_name"] = pdf_path.name
    meta["csv_name"] = csv_path.name
    return meta


INDEX_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Prospector Reports — Antek Automation</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;800;900&family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --coral: #CD5C3C; --cream: #E8DCC8; --sage: #C8D8D0;
            --charcoal: #2C2C2C; --black: #000000; --off-white: #FAF8F5;
        }
        body { background: var(--cream); color: var(--charcoal); font-family: 'DM Sans', sans-serif; font-size: 16px; line-height: 1.6; }
        .site-header { background: var(--charcoal); border-bottom: 3px solid var(--black); padding: 0 48px; height: 56px; display: flex; align-items: center; justify-content: space-between; }
        .header-brand { font-family: 'Outfit', sans-serif; font-weight: 800; font-size: 14px; color: var(--cream); text-transform: uppercase; letter-spacing: 0.1em; }
        .header-meta { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--coral); }
        .hero { padding: 40px 48px 32px; border-bottom: 3px solid var(--black); }
        .eyebrow { display: block; font-family: 'JetBrains Mono', monospace; font-size: 11px; text-transform: uppercase; letter-spacing: 0.15em; color: var(--coral); margin-bottom: 14px; }
        .hero h1 { font-family: 'Outfit', sans-serif; font-weight: 900; font-size: 40px; line-height: 1.02; margin-bottom: 10px; }
        .hero .sub { font-size: 14px; opacity: 0.75; max-width: 620px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 14px 20px; border-bottom: 2px solid var(--black); font-size: 14px; }
        th { font-family: 'JetBrains Mono', monospace; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; background: var(--charcoal); color: var(--cream); border-bottom: 3px solid var(--black); }
        tr:hover td { background: var(--off-white); }
        td.run-name { font-family: 'Outfit', sans-serif; font-weight: 700; }
        a.run-link { color: var(--coral); text-decoration: none; font-weight: 700; }
        a.run-link:hover { text-decoration: underline; }
        .dl { font-family: 'JetBrains Mono', monospace; font-size: 11px; }
        .dl a { color: var(--charcoal); text-decoration: none; margin-right: 10px; opacity: 0.75; }
        .dl a:hover { opacity: 1; text-decoration: underline; }
        .site-footer { padding: 20px 48px; display: flex; justify-content: space-between; align-items: center; gap: 24px; font-family: 'JetBrains Mono', monospace; font-size: 10.5px; opacity: 0.55; }
        .section { padding: 8px 48px 48px; }
    </style>
</head>"""


def render_index(run_metas: list[dict]) -> str:
    date_str = datetime.now(timezone.utc).strftime("%-d %B %Y")
    rows = []
    for m in sorted(run_metas, key=lambda r: r["id"], reverse=True):
        sectors = ", ".join(m["sectors"]) or "—"
        created = (m["created_at"] or "")[:10]
        rows.append(f"""\
        <tr>
            <td class="run-name">Run #{m['id']}</td>
            <td>{escape(m['area'] or '—')}</td>
            <td>{escape(sectors)}</td>
            <td>{created}</td>
            <td>{m['target_count']}</td>
            <td><a class="run-link" href="runs/{m['id']}/index.html">View report &rarr;</a></td>
            <td class="dl"><a href="runs/{m['id']}/{escape(m['pdf_name'])}" download>PDF</a><a href="runs/{m['id']}/{escape(m['csv_name'])}" download>CSV</a></td>
        </tr>""")

    body = f"""\
<body>
    <header class="site-header">
        <div class="header-brand">Antek Automation — Prospector</div>
        <div class="header-meta">{date_str}</div>
    </header>
    <section class="hero">
        <span class="eyebrow">All runs</span>
        <h1>Prospector reports</h1>
        <p class="sub">Every prospecting run generated so far, with the live branded report and direct PDF/CSV downloads for each.</p>
    </section>
    <section class="section">
        <table>
            <thead>
                <tr><th>Run</th><th>Location</th><th>Vertical</th><th>Date</th><th>Targets</th><th>Report</th><th>Downloads</th></tr>
            </thead>
            <tbody>
{''.join(rows)}
            </tbody>
        </table>
    </section>
    <footer class="site-footer">
        <div>PROSPECTOR</div>
        <div>Antek Automation — internal outreach snapshot index</div>
    </footer>
</body>
</html>"""
    return INDEX_HEAD + "\n" + body


def build_site(conn, dist_dir: Path = DIST_DIR, extra_run_id: int | None = None,
                force_regenerate_extra: bool = False) -> list[dict]:
    """Rebuild the whole static site into dist_dir. extra_run_id (if given)
    is generated fresh (force_regenerate_extra=True forces a re-render even
    if artifacts already exist) — every other run with an existing PDF in
    reports/runs/ is included as-is (fast, no re-render)."""
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)

    run_ids = discover_deployable_run_ids(conn)
    if extra_run_id is not None and extra_run_id not in run_ids:
        run_ids.append(extra_run_id)
    run_ids = sorted(set(run_ids))

    metas = []
    for rid in run_ids:
        force = force_regenerate_extra and rid == extra_run_id
        try:
            metas.append(build_run_page(conn, rid, dist_dir, force_regenerate=force))
        except SystemExit as e:
            print(f"Skipping run {rid}: {e}", file=sys.stderr)

    (dist_dir / "index.html").write_text(render_index(metas), encoding="utf-8")
    return metas


def wrangler_deploy(dist_dir: Path, project_name: str = PAGES_PROJECT) -> str:
    """Deploy dist_dir to Cloudflare Pages via `npx wrangler pages deploy`.
    Creates the project first if it doesn't exist yet. Returns the
    deployment URL wrangler reports (stdout)."""
    env = os.environ.copy()
    if not env.get("CLOUDFLARE_API_TOKEN") or not env.get("CLOUDFLARE_ACCOUNT_ID"):
        raise SystemExit(
            "CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID missing from environment "
            "(expected in .env, loaded via prospector.config)."
        )

    existing = subprocess.run(
        ["npx", "wrangler", "pages", "project", "list"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    if project_name not in (existing.stdout or ""):
        create = subprocess.run(
            ["npx", "wrangler", "pages", "project", "create", project_name,
             "--production-branch", "main"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        if create.returncode != 0:
            print(create.stdout, file=sys.stderr)
            print(create.stderr, file=sys.stderr)
            raise SystemExit(f"Failed to create Cloudflare Pages project {project_name!r}.")

    result = subprocess.run(
        ["npx", "wrangler", "pages", "deploy", str(dist_dir), "--project-name", project_name,
         "--branch", "main", "--commit-dirty=true"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("wrangler pages deploy failed.")

    out = (result.stdout or "") + (result.stderr or "")
    m = re.search(r"https://[a-zA-Z0-9.-]+\.pages\.dev\S*", out)
    return m.group(0) if m else f"https://{project_name}.pages.dev"


def deploy_run(conn, run_id: int, force_regenerate: bool = False, project_name: str = PAGES_PROJECT) -> dict:
    """Full flow for `prospector deploy --run-id N` / `prospector report
    --run-id N --deploy`: rebuild the whole site (this run + every other
    run that already has artifacts) and push it to Cloudflare Pages."""
    metas = build_site(conn, extra_run_id=run_id, force_regenerate_extra=force_regenerate)
    base_url = wrangler_deploy(DIST_DIR, project_name=project_name)
    run_meta = next((m for m in metas if m["id"] == run_id), None)
    return {
        "base_url": base_url,
        "index_url": f"{base_url}/index.html",
        "run_url": f"{base_url}/runs/{run_id}/index.html" if run_meta else None,
        "run_ids": [m["id"] for m in metas],
    }
