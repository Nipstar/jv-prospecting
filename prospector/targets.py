"""Combined targeting and export — Prospector v2 Phase 5.

Pulls together everything discovery/places.py (Phase 2),
enrichers/reviews.py (Phase 3), and enrichers/site.py (Phase 4) wrote to
`businesses`, sorted primary by review_target_score descending, tiebreak
opportunity_score descending (per Andy's spec) — the weak-review-profile
signal is the primary targeting criterion; opportunity_score (missing
booking/chat/callback wording) is a secondary tiebreak among equally
weak-review firms, not a targeting criterion on its own.

PECR / TPS compliance note (see README "Compliance" section for the full
version Andy asked for): outreach here is phone-first against corporate
numbers, which still requires screening against the Telephone Preference
Service / Corporate TPS before calling under PECR. `tps_checked` is a
compliance placeholder column, defaulting to false — there is no TPS API
integration in this tool; it's a column + a written reminder, per spec.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

from prospector.config import EXPORTS_DIR

TARGET_LIST_COLUMNS = [
    "id", "name", "vertical", "town", "phone", "review_count", "rating",
    "review_target_score", "opportunity_score", "director_name",
]

EXPORT_COLUMNS = [
    "firm_name", "vertical", "location", "postcode", "phone", "email", "website",
    "review_count", "avg_rating", "review_target_score", "opportunity_score",
    "flags", "company_number", "director_name", "years_trading", "tps_checked",
    "is_chain", "chain_reason",
]

_ORDER_SQL = "ORDER BY review_target_score DESC, opportunity_score DESC"

# Andy's standing exclusion rule: no corporate/franchise/chain businesses —
# the pitch (AI call answering + review generation) targets owner-operators
# who feel a missed call personally, not corporate entities with dedicated
# marketing/reception teams. `is_chain` (prospector/chain_signals.py +
# discovery/places.py) is computed at discovery/enrichment time and never
# deletes data — `targets list`/`export` just exclude it from the default
# view. Pass include_chains=True (CLI: --include-chains) to see/export
# flagged businesses anyway, e.g. to audit why something got flagged.


def _flags_for(row: sqlite3.Row) -> str:
    flag_names = []
    if row["weak_gbp"]:
        flag_names.append("weak_gbp")
    if row["has_negative_recent"]:
        flag_names.append("has_negative_recent")
    if row["missed_call_evidence"]:
        flag_names.append("missed_call_evidence")
    if row["no_booking"]:
        flag_names.append("no_booking")
    if row["no_chat"]:
        flag_names.append("no_chat")
    if row["phone_dependent"]:
        flag_names.append("phone_dependent")
    if row["established_flag"]:
        flag_names.append("established")
    if row["is_group_owned"]:
        flag_names.append("group_owned")
    if row["is_chain"]:
        flag_names.append("chain")
    return ";".join(flag_names)


def _years_trading(incorporation_date: str | None) -> str:
    if not incorporation_date:
        return ""
    try:
        d = datetime.strptime(incorporation_date, "%Y-%m-%d").date()
    except ValueError:
        return ""
    return str(int((date.today() - d).days / 365.25))


def list_targets(
    conn: sqlite3.Connection,
    min_score: int = 0,
    run_id: int | None = None,
    include_chains: bool = False,
) -> list[sqlite3.Row]:
    query = (
        "SELECT * FROM businesses "
        "WHERE review_target_score IS NOT NULL AND review_target_score >= ?"
    )
    params: list = [min_score]
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    if not include_chains:
        query += " AND (is_chain IS NULL OR is_chain = 0)"
    query += f" {_ORDER_SQL}"
    return conn.execute(query, params).fetchall()


def export_targets_csv(
    conn: sqlite3.Connection,
    out_path: Path | None = None,
    min_score: int = 0,
    run_id: int | None = None,
    include_chains: bool = False,
) -> Path:
    rows = list_targets(conn, min_score=min_score, run_id=run_id, include_chains=include_chains)

    if out_path is None:
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        out_path = EXPORTS_DIR / f"targets_{timestamp}.csv"

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow([
                row["name"],
                row["vertical"],
                row["town"],
                row["postcode"],
                row["phone"],
                row["email"],
                row["website"],
                row["review_count"],
                row["rating"],
                row["review_target_score"],
                row["opportunity_score"],
                _flags_for(row),
                row["companies_house_number"],
                row["director_name"] or "",
                _years_trading(row["incorporation_date"]),
                "true" if row["tps_checked"] else "false",
                "true" if row["is_chain"] else "false",
                row["chain_reason"] or "",
            ])

    return out_path
