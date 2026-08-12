"""SQLite schema + lightweight migrations for prospector."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from prospector.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    created_at TEXT,
    area TEXT,
    trade_sectors TEXT,       -- JSON array of sectors run in this batch
    notes TEXT
);

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    name TEXT,
    vertical TEXT,
    town TEXT,
    postcode TEXT,
    address TEXT,
    website TEXT,
    domain TEXT,
    phone TEXT,
    google_place_id TEXT,
    rating REAL,
    review_count INTEGER,
    director_name TEXT,
    companies_house_number TEXT,
    is_group_owned INTEGER,        -- 1/0, from PSC/officer check
    priority TEXT,                 -- A / B / C
    priority_score INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id),
    rating INTEGER,
    text TEXT,
    review_date TEXT,
    pain_flag INTEGER   -- 1/0
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT
);
"""

# Ordered list of (version, migration) applied after the base schema, each
# tracked (once) in schema_migrations. A migration is either a raw SQL
# string (executed via executescript) or a callable taking the open
# connection, for migrations that need to check existing state before
# acting (e.g. conditionally dropping columns that may or may not exist
# depending on whether this is a fresh DB or an upgrade from an older one).
#
# v1 — the base schema originally shipped without runs.pending_apify_runs;
# add it for any prospector.db created before the (now-removed) Apify
# async-collection feature existed. Kept as historical/no-op-safe so old
# DBs still upgrade cleanly; v2 below removes the column again for
# everyone, since Phase 1 of the v2 rebuild removed the ad-spend module
# this supported.
#
# v2 — Phase 1 of the "Prospector v2: UK High-Ticket Firms, Review-Based
# Targeting" rebuild removed the ad-spend module (Facebook Ads Library +
# Google Ads Transparency via Apify) entirely. This migration drops the
# now-unused ad-spend columns from `businesses` and the `pending_apify_runs`
# async-collection column from `runs`. Uses native `ALTER TABLE ... DROP
# COLUMN` (supported since SQLite 3.35; this environment runs 3.40.1).
# Columns are dropped conditionally (checked via PRAGMA table_info) so this
# is safe both for the 8 pre-existing real runs (which have the columns)
# and for a brand-new DB (whose CREATE TABLE no longer defines them, but
# which still gets a pending_apify_runs column from v1 above and needs it
# dropped too).
_V2_DROPPED_BUSINESS_COLUMNS = [
    "fb_ads_active", "fb_ads_creative_count", "fb_ads_earliest_seen",
    "google_ads_active", "google_ads_creative_count", "google_ads_days_active",
    "google_ads_advertiser_name",
]


def _migration_v2_drop_ad_columns(conn: sqlite3.Connection) -> None:
    existing_business_cols = {row[1] for row in conn.execute("PRAGMA table_info(businesses)")}
    for col in _V2_DROPPED_BUSINESS_COLUMNS:
        if col in existing_business_cols:
            conn.execute(f"ALTER TABLE businesses DROP COLUMN {col}")

    existing_run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    if "pending_apify_runs" in existing_run_cols:
        conn.execute("ALTER TABLE runs DROP COLUMN pending_apify_runs")


MIGRATIONS: list[tuple[int, "str | object"]] = [
    (1, "ALTER TABLE runs ADD COLUMN pending_apify_runs TEXT DEFAULT '[]';"),
    (2, _migration_v2_drop_ad_columns),
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create tables if they don't exist and apply any pending migrations."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        applied = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, migration in MIGRATIONS:
            if version in applied:
                continue
            if callable(migration):
                migration(conn)
            else:
                conn.executescript(migration)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _utcnow()),
            )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_run(conn: sqlite3.Connection, area: str, trade_sectors: list[str], notes: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO runs (created_at, area, trade_sectors, notes) VALUES (?, ?, ?, ?)",
        (_utcnow(), area, json.dumps(trade_sectors), notes),
    )
    return int(cur.lastrowid)


def insert_business(conn: sqlite3.Connection, run_id: int, biz: dict) -> int:
    cols = [
        "run_id", "name", "vertical", "town", "postcode", "address", "website",
        "domain", "phone", "google_place_id", "rating", "review_count",
        "director_name", "companies_house_number", "is_group_owned",
        "priority", "priority_score", "created_at",
    ]
    values = [run_id] + [biz.get(c) for c in cols[1:-1]] + [_utcnow()]
    placeholders = ", ".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO businesses ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )
    return int(cur.lastrowid)


def insert_review(conn: sqlite3.Connection, business_id: int, review: dict) -> int:
    cur = conn.execute(
        "INSERT INTO reviews (business_id, rating, text, review_date, pain_flag) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            business_id,
            review.get("rating"),
            review.get("text"),
            review.get("review_date"),
            1 if review.get("pain_flag") else 0,
        ),
    )
    return int(cur.lastrowid)


def has_pain_flagged_review(conn: sqlite3.Connection, business_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE business_id = ? AND pain_flag = 1",
        (business_id,),
    ).fetchone()
    return bool(row[0])


def update_business_priority(conn: sqlite3.Connection, business_id: int, priority: str, priority_score: int) -> None:
    conn.execute(
        "UPDATE businesses SET priority = ?, priority_score = ? WHERE id = ?",
        (priority, priority_score, business_id),
    )
