"""CSV export — `prospector export --run-id N --format csv`.

Writes ./exports/run_{N}_{timestamp}.csv with all businesses columns plus a
top_pain_review column (first pain-flagged review text per business,
truncated to 200 chars). Sorted by priority (A first) then priority_score
descending.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from prospector.config import EXPORTS_DIR

PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2}

BUSINESS_COLUMNS = [
    "id", "run_id", "name", "vertical", "town", "postcode", "address", "website",
    "domain", "phone", "google_place_id", "rating", "review_count",
    "director_name", "companies_house_number", "is_group_owned",
    "is_chain", "chain_reason",
    "priority", "priority_score", "created_at",
]


def _top_pain_review(conn: sqlite3.Connection, business_id: int) -> str:
    row = conn.execute(
        "SELECT text FROM reviews WHERE business_id = ? AND pain_flag = 1 "
        "ORDER BY id LIMIT 1",
        (business_id,),
    ).fetchone()
    if not row or not row["text"]:
        return ""
    text = row["text"]
    return text[:200] + ("..." if len(text) > 200 else "")


def export_run_csv(conn: sqlite3.Connection, run_id: int) -> Path:
    rows = conn.execute(
        f"SELECT {', '.join(BUSINESS_COLUMNS)} FROM businesses WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    def sort_key(row: sqlite3.Row):
        return (PRIORITY_ORDER.get(row["priority"], 99), -(row["priority_score"] or 0))

    rows = sorted(rows, key=sort_key)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = EXPORTS_DIR / f"run_{run_id}_{timestamp}.csv"

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BUSINESS_COLUMNS + ["top_pain_review"])
        for row in rows:
            values = [row[c] for c in BUSINESS_COLUMNS]
            values.append(_top_pain_review(conn, row["id"]))
            writer.writerow(values)

    return out_path
