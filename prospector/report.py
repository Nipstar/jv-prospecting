"""Branded PDF reports — `prospector report --run-id N` / `--business-id N`.

Borrows the rendering approach and brand system built for geo-slab
(/data/workspaces/worker/geo-slab/scripts/generate_prospect_report.py):

  - Self-contained HTML + CSS "brutalist" template (Outfit 800 headlines,
    DM Sans body, JetBrains Mono labels/eyebrows/footers; coral #CD5C3C on
    cream #E8DCC8 / charcoal #2C2C2C — Andy's brand system, same as his
    podcast cover branding and the geo-slab prospect scan).
  - HTML -> PDF via Playwright headless Chromium (`page.pdf()`), not
    WeasyPrint/wkhtmltopdf, so it matches the geo-slab rendering pipeline
    exactly and the PDF is pixel-identical to the HTML.

geo-slab's template is GEO-audit-shaped (0-100 scores, AI citability, etc.)
which doesn't map onto prospector's data. This module ports the *mechanism*
(HTML template -> Playwright -> PDF, same fonts/palette/card language) and
defines two prospector-specific layouts:

  - Run report: one-page-per-N "top prospects" snapshot for a whole run —
    priority mix, then a card per business (name/address/priority/score/
    ownership/review pain signal/top pain quote/contact).
  - Business report: a single-business one-page sales-readiness snapshot,
    for when you want to hand one prospect's findings to a rep.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from prospector.config import REPORTS_DIR

PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2}

PRIORITY_LABEL = {
    "A": "Priority A — pain signal and independently owned",
    "B": "Priority B — pain signal or independently owned",
    "C": "Priority C — qualified, lower urgency",
}


# ── Brand system (ported from geo-slab/scripts/generate_prospect_report.py) ──

BRAND_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;800;900&family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        @page {{ size: A4; margin: 0; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --coral: #CD5C3C;
            --cream: #E8DCC8;
            --sage: #C8D8D0;
            --charcoal: #2C2C2C;
            --black: #000000;
            --off-white: #FAF8F5;
            --shadow: 8px 8px 0 var(--black);
        }}
        html {{ scroll-behavior: smooth; }}
        body {{
            background: var(--cream);
            color: var(--charcoal);
            font-family: 'DM Sans', sans-serif;
            font-size: 16px;
            line-height: 1.6;
        }}
        h1, h2, h3 {{ page-break-after: avoid; break-after: avoid-page; }}
        .site-header {{
            background: var(--charcoal);
            border-bottom: 3px solid var(--black);
            padding: 0 48px;
            height: 56px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .header-brand {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 14px;
            color: var(--cream);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        .header-meta {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: var(--coral);
        }}
        .hero {{
            padding: 40px 48px 32px;
            border-bottom: 3px solid var(--black);
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .eyebrow {{
            display: block;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            color: var(--coral);
            margin-bottom: 14px;
        }}
        .hero h1 {{
            font-family: 'Outfit', sans-serif;
            font-weight: 900;
            font-size: 40px;
            line-height: 1.02;
            margin-bottom: 10px;
        }}
        .hero .sub {{
            font-size: 14px;
            opacity: 0.75;
            max-width: 620px;
        }}
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            border-bottom: 3px solid var(--black);
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .stat-cell {{
            border-right: 3px solid var(--black);
            padding: 20px 20px 18px;
            background: var(--off-white);
        }}
        .stat-cell:last-child {{ border-right: none; }}
        .stat-cell.priority-a {{ background: var(--coral); }}
        .stat-cell.priority-a .stat-label,
        .stat-cell.priority-a .stat-num {{ color: var(--cream); }}
        .stat-label {{
            display: block;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            opacity: 0.6;
            margin-bottom: 10px;
        }}
        .stat-num {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 40px;
            line-height: 1;
        }}
        .section {{
            border-bottom: 3px solid var(--black);
            padding: 28px 48px;
        }}
        .section-label {{
            display: block;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            color: var(--coral);
            margin-bottom: 14px;
        }}
        .section h2 {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 30px;
            line-height: 1;
            margin-bottom: 20px;
        }}
        .biz-card {{
            border: 3px solid var(--black);
            border-bottom: none;
            padding: 22px 26px;
            background: var(--off-white);
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .biz-card:last-child {{ border-bottom: 3px solid var(--black); }}
        .biz-card.priority-a {{ background: #f4e2da; }}
        .biz-card-top {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 6px;
        }}
        .biz-name {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 19px;
            line-height: 1.15;
        }}
        .biz-score {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 700;
            white-space: nowrap;
        }}
        .tag-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin: 8px 0 10px;
        }}
        .tag {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 9.5px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            padding: 3px 8px;
            border: 1.5px solid var(--charcoal);
            color: var(--charcoal);
        }}
        .tag.on {{ background: var(--charcoal); color: var(--cream); border-color: var(--charcoal); }}
        .tag.priority {{ background: var(--coral); color: var(--cream); border-color: var(--coral); font-weight: 700; }}
        .biz-meta {{
            font-size: 12.5px;
            opacity: 0.75;
            margin-bottom: 8px;
        }}
        .biz-meta span + span::before {{ content: " · "; opacity: 0.5; }}
        .biz-quote {{
            border-left: 3px solid var(--coral);
            background: rgba(205,92,60,0.08);
            padding: 8px 14px;
            font-size: 12.5px;
            font-style: italic;
            opacity: 0.9;
            margin-top: 6px;
        }}
        .biz-quote .quote-label {{
            display: block;
            font-family: 'JetBrains Mono', monospace;
            font-style: normal;
            font-size: 9.5px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--coral);
            margin-bottom: 4px;
        }}
        /* Score hero (single-business report) */
        .score-hero {{
            padding: 40px 48px;
            border-bottom: 3px solid var(--black);
            display: grid;
            grid-template-columns: 220px 1fr;
            gap: 48px;
            align-items: start;
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .score-big {{
            font-family: 'Outfit', sans-serif;
            font-weight: 900;
            font-size: 110px;
            line-height: 0.85;
            color: var(--coral);
        }}
        .score-denom {{
            font-family: 'Outfit', sans-serif;
            font-weight: 700;
            font-size: 26px;
            opacity: 0.35;
        }}
        .signal-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            border-bottom: 3px solid var(--black);
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .signal-cell {{
            border-right: 3px solid var(--black);
            padding: 18px 20px;
            background: var(--off-white);
        }}
        .signal-cell:last-child {{ border-right: none; }}
        .signal-cell.active {{ background: var(--sage); }}
        .signal-label {{
            display: block;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            opacity: 0.6;
            margin-bottom: 8px;
        }}
        .signal-value {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 20px;
        }}
        .contact-section {{
            background: var(--charcoal);
            color: var(--cream);
            padding: 32px 48px;
        }}
        .contact-row {{ font-size: 14px; margin: 4px 0; opacity: 0.9; }}
        .contact-row strong {{ font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--coral); text-transform: uppercase; font-size: 10px; letter-spacing: 0.08em; margin-right: 10px; }}
        .site-footer {{
            padding: 20px 48px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 24px;
            break-inside: avoid;
            page-break-inside: avoid;
        }}
        .footer-brand {{
            font-family: 'Outfit', sans-serif;
            font-weight: 800;
            font-size: 15px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .footer-meta {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 10.5px;
            opacity: 0.45;
            text-align: right;
        }}
    </style>
</head>"""


def _fmt_date(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%-d %B %Y")


def _tag(label: str, on: bool) -> str:
    cls = "tag on" if on else "tag"
    return f'<span class="{cls}">{escape(label)}</span>'


def _ownership_pain_tags(row: sqlite3.Row, has_pain: bool) -> str:
    tags = [
        _tag("Independent" if not row["is_group_owned"] else "Group-owned", not row["is_group_owned"]),
        _tag("Pain-flagged review" if has_pain else "No pain-flagged review", has_pain),
    ]
    return "\n".join(tags)


def _top_pain_review(conn: sqlite3.Connection, business_id: int) -> str:
    row = conn.execute(
        "SELECT text FROM reviews WHERE business_id = ? AND pain_flag = 1 ORDER BY id LIMIT 1",
        (business_id,),
    ).fetchone()
    if not row or not row["text"]:
        return ""
    text = row["text"]
    return text[:280] + ("..." if len(text) > 280 else "")


# ── Data fetch ───────────────────────────────────────────────────────────────

def fetch_run_data(conn: sqlite3.Connection, run_id: int, limit: int | None = None) -> dict:
    run = conn.execute(
        "SELECT id, created_at, area, trade_sectors, notes FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not run:
        raise SystemExit(f"No run found with id={run_id}.")

    rows = conn.execute(
        "SELECT * FROM businesses WHERE run_id = ?", (run_id,)
    ).fetchall()
    if not rows:
        raise SystemExit(f"No businesses found for run_id={run_id}. Nothing to report.")

    def sort_key(row: sqlite3.Row):
        return (PRIORITY_ORDER.get(row["priority"], 99), -(row["priority_score"] or 0))

    rows = sorted(rows, key=sort_key)
    if limit:
        rows = rows[:limit]

    counts = {"A": 0, "B": 0, "C": 0}
    for row in conn.execute("SELECT priority FROM businesses WHERE run_id = ?", (run_id,)).fetchall():
        if row["priority"] in counts:
            counts[row["priority"]] += 1

    businesses = []
    for row in rows:
        businesses.append({
            "row": row,
            "top_pain_review": _top_pain_review(conn, row["id"]),
        })

    return {
        "run_id": run_id,
        "area": run["area"],
        "trade_sectors": run["trade_sectors"],
        "created_at": run["created_at"],
        "notes": run["notes"],
        "counts": counts,
        "total": sum(counts.values()),
        "businesses": businesses,
    }


def fetch_business_data(conn: sqlite3.Connection, business_id: int) -> dict:
    row = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not row:
        raise SystemExit(f"No business found with id={business_id}.")
    return {
        "row": row,
        "top_pain_review": _top_pain_review(conn, business_id),
    }


# ── Rendering ────────────────────────────────────────────────────────────────

def _biz_card(entry: dict) -> str:
    row = entry["row"]
    priority = row["priority"] or "C"
    cls = " priority-a" if priority == "A" else ""
    name = escape(row["name"] or "Unnamed business")
    score = row["priority_score"] or 0
    addr_bits = [b for b in [row["address"], row["town"], row["postcode"]] if b]
    address = escape(", ".join(addr_bits)) if addr_bits else ""
    meta_bits = []
    if row["rating"]:
        meta_bits.append(f'<span>{row["rating"]}★ ({row["review_count"] or 0} reviews)</span>')
    if row["phone"]:
        meta_bits.append(f'<span>{escape(row["phone"])}</span>')
    if row["website"]:
        meta_bits.append(f'<span>{escape(row["website"])}</span>')
    if row["director_name"]:
        meta_bits.append(f'<span>Director: {escape(row["director_name"])}</span>')
    meta = "".join(meta_bits)

    quote = ""
    if entry["top_pain_review"]:
        quote = (
            '<div class="biz-quote"><span class="quote-label">Pain-flagged review</span>'
            f'&ldquo;{escape(entry["top_pain_review"])}&rdquo;</div>'
        )

    return f"""\
        <div class="biz-card{cls}">
            <div class="biz-card-top">
                <div class="biz-name">{name}{f' &mdash; <span style="opacity:.6;font-weight:500;font-size:13px">{escape(row["vertical"])}</span>' if row["vertical"] else ''}</div>
                <div class="biz-score">SCORE {score}</div>
            </div>
            <div class="biz-meta">{meta}</div>
            <div class="tag-row">
                <span class="tag priority">PRIORITY {escape(priority)}</span>
                {_ownership_pain_tags(row, bool(entry["top_pain_review"]))}
            </div>
            {f'<div class="biz-meta" style="margin-top:2px">{address}</div>' if address else ''}
            {quote}
        </div>"""


def render_run_report(data: dict) -> str:
    area = escape(data["area"] or "Unspecified area")
    date = _fmt_date()
    counts = data["counts"]
    total = data["total"]
    shown = len(data["businesses"])

    try:
        import json as _json
        sectors = _json.loads(data["trade_sectors"] or "[]")
    except Exception:
        sectors = []
    sectors_str = escape(", ".join(sectors)) if sectors else "all sectors"

    head = BRAND_HEAD.format(title=f"Prospector Report — Run #{data['run_id']}")

    cards = "\n".join(_biz_card(e) for e in data["businesses"])

    subtitle = (
        f"{total} qualified businesses across {sectors_str}. "
        f"Showing top {shown} sorted by priority tier, then score."
        if shown < total else
        f"{total} qualified businesses across {sectors_str}, sorted by priority tier, then score."
    )

    body = f"""\
<body>
    <header class="site-header">
        <div class="header-brand">Antek Automation — Prospector</div>
        <div class="header-meta">Run #{data['run_id']} &mdash; {date}</div>
    </header>

    <section class="hero">
        <span class="eyebrow">Sales-readiness snapshot</span>
        <h1>Top prospects &mdash; {area}</h1>
        <p class="sub">{subtitle}</p>
    </section>

    <div class="stat-grid">
        <div class="stat-cell priority-a">
            <span class="stat-label">Priority A</span>
            <div class="stat-num">{counts['A']}</div>
        </div>
        <div class="stat-cell">
            <span class="stat-label">Priority B</span>
            <div class="stat-num">{counts['B']}</div>
        </div>
        <div class="stat-cell">
            <span class="stat-label">Priority C</span>
            <div class="stat-num">{counts['C']}</div>
        </div>
        <div class="stat-cell">
            <span class="stat-label">Total qualified</span>
            <div class="stat-num">{total}</div>
        </div>
    </div>

    <section class="section">
        <span class="section-label">Ranked prospect list</span>
        <h2>who to call first</h2>
        <div>
{cards}
        </div>
    </section>

    <footer class="site-footer">
        <div class="footer-brand">PROSPECTOR</div>
        <div class="footer-meta">
            Run #{data['run_id']} &mdash; {date}<br>
            Antek Automation &mdash; internal outreach snapshot
        </div>
    </footer>
</body>
</html>"""

    return head + "\n" + body


def render_business_report(entry: dict) -> str:
    row = entry["row"]
    name = escape(row["name"] or "Unnamed business")
    priority = row["priority"] or "C"
    score = row["priority_score"] or 0
    date = _fmt_date()

    head = BRAND_HEAD.format(title=f"Prospector Snapshot — {row['name'] or 'Business'}")

    addr_bits = [b for b in [row["address"], row["town"], row["postcode"]] if b]
    address = escape(", ".join(addr_bits)) if addr_bits else "Address not captured"

    def signal_cell(label: str, value: str, active: bool) -> str:
        cls = "signal-cell active" if active else "signal-cell"
        return f"""\
        <div class="{cls}">
            <span class="signal-label">{escape(label)}</span>
            <div class="signal-value">{escape(value)}</div>
        </div>"""

    has_pain = bool(entry["top_pain_review"])
    pain_val = "Pain-flagged review found" if has_pain else "No pain-flagged review"

    rating_val = f'{row["rating"]}★ ({row["review_count"] or 0} reviews)' if row["rating"] else "Not captured"

    ownership_val = "Group / corporate owned" if row["is_group_owned"] else "Independently owned"

    signal_grid = "\n".join([
        signal_cell("Reviews", rating_val, False),
        signal_cell("Review pain signal", pain_val, has_pain),
        signal_cell("Ownership", ownership_val, not row["is_group_owned"]),
    ])

    quote_block = ""
    if entry["top_pain_review"]:
        quote_block = f"""\
    <section class="section">
        <span class="section-label">Signal</span>
        <h2>a pain-flagged review</h2>
        <div class="biz-quote" style="font-size:15px;padding:16px 22px;">
            <span class="quote-label">From Google reviews</span>
            &ldquo;{escape(entry['top_pain_review'])}&rdquo;
        </div>
    </section>"""

    contact_rows = []
    if row["phone"]:
        contact_rows.append(f'<div class="contact-row"><strong>Phone</strong>{escape(row["phone"])}</div>')
    if row["website"]:
        contact_rows.append(f'<div class="contact-row"><strong>Website</strong>{escape(row["website"])}</div>')
    if row["director_name"]:
        contact_rows.append(f'<div class="contact-row"><strong>Director</strong>{escape(row["director_name"])}</div>')
    if row["companies_house_number"]:
        contact_rows.append(f'<div class="contact-row"><strong>Companies House</strong>{escape(row["companies_house_number"])}</div>')
    contact_html = "\n".join(contact_rows) or '<div class="contact-row">No contact details captured.</div>'

    body = f"""\
<body>
    <header class="site-header">
        <div class="header-brand">Antek Automation — Prospector</div>
        <div class="header-meta">Sales snapshot &mdash; {date}</div>
    </header>

    <section class="score-hero">
        <div>
            <span class="score-big">{score}</span>
            <span class="score-denom">priority score</span>
        </div>
        <div>
            <span class="eyebrow">{escape(PRIORITY_LABEL.get(priority, 'Priority ' + priority))}</span>
            <h1 style="font-family:'Outfit',sans-serif;font-weight:900;font-size:32px;line-height:1.05;margin-bottom:10px;">{name}</h1>
            <p style="font-size:14px;opacity:.75;max-width:520px;">{address}{' &middot; ' + escape(row['vertical']) if row['vertical'] else ''}</p>
        </div>
    </section>

    <div class="signal-grid">
{signal_grid}
    </div>

{quote_block}

    <section class="contact-section">
        <span class="eyebrow" style="color:var(--coral)">Contact</span>
{contact_html}
    </section>

    <footer class="site-footer">
        <div class="footer-brand">PROSPECTOR</div>
        <div class="footer-meta">
            Business #{row['id']} &mdash; run #{row['run_id']} &mdash; {date}<br>
            Antek Automation &mdash; internal outreach snapshot
        </div>
    </footer>
</body>
</html>"""

    return head + "\n" + body


# ── PDF rendering (ported mechanism: Playwright headless Chromium print) ────

def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    """Print an HTML file to PDF using Playwright headless Chromium — same
    mechanism as geo-slab/scripts/generate_prospect_report.py."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: Playwright is required for PDF output.\n"
            "  pip install playwright\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"file://{html_path.resolve()}", wait_until="networkidle")
        try:
            page.evaluate("document.fonts.ready")
        except Exception:
            pass
        page.emulate_media(media="print")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
        )
        browser.close()


def _safe_slug(text: str) -> str:
    return re.sub(r"[^\w.-]", "-", text).strip("-") or "report"


def generate_run_report(conn: sqlite3.Connection, run_id: int, limit: int | None = None,
                         output_dir: Path | None = None) -> tuple[Path, Path]:
    output_dir = output_dir or REPORTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    data = fetch_run_data(conn, run_id, limit=limit)
    html = render_run_report(data)

    area_slug = _safe_slug(data["area"] or "run")
    html_path = output_dir / f"PROSPECTOR-RUN-{run_id}-{area_slug}.html"
    pdf_path = html_path.with_suffix(".pdf")

    html_path.write_text(html, encoding="utf-8")
    html_to_pdf(html_path, pdf_path)
    return html_path, pdf_path


def generate_business_report(conn: sqlite3.Connection, business_id: int,
                              output_dir: Path | None = None) -> tuple[Path, Path]:
    output_dir = output_dir or REPORTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    entry = fetch_business_data(conn, business_id)
    html = render_business_report(entry)

    name_slug = _safe_slug(entry["row"]["name"] or f"business-{business_id}")
    html_path = output_dir / f"PROSPECTOR-BIZ-{business_id}-{name_slug}.html"
    pdf_path = html_path.with_suffix(".pdf")

    html_path.write_text(html, encoding="utf-8")
    html_to_pdf(html_path, pdf_path)
    return html_path, pdf_path
