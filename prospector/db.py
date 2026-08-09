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
    -- pending_apify_runs TEXT added by migration v1 below (JSON list of
    -- Apify runs still going after the 60s sync-poll window, to be picked
    -- up later by `prospector collect`)
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
    fb_ads_active INTEGER,         -- 1/0/NULL. NULL = unknown/couldn't
                                    -- check (Apify run failed, timed out,
                                    -- or came back empty); 0 = checked,
                                    -- confirmed not found. See
                                    -- prospector/apify_client.py.
    fb_ads_creative_count INTEGER,
    fb_ads_earliest_seen TEXT,
    google_ads_active INTEGER,     -- 1/0/NULL, same NULL-vs-0 semantics as
                                    -- fb_ads_active above.
    google_ads_creative_count INTEGER,
    google_ads_days_active INTEGER,
    google_ads_advertiser_name TEXT,  -- flag if != business name (agency-run)
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

# Ordered list of (version, sql) migrations applied after the base schema.
# v1 — the base schema originally shipped without runs.pending_apify_runs;
# add it for any prospector.db created before the Apify async-collection
# feature existed. CREATE TABLE IF NOT EXISTS above already includes the
# column for brand-new databases, so this is a no-op there (guarded by
# schema_migrations) but required for upgrading an existing file.
MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE runs ADD COLUMN pending_apify_runs TEXT DEFAULT '[]';"),
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
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            conn.executescript(sql)
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
        "fb_ads_active", "fb_ads_creative_count", "fb_ads_earliest_seen",
        "google_ads_active", "google_ads_creative_count", "google_ads_days_active",
        "google_ads_advertiser_name", "priority", "priority_score", "created_at",
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


# --- Pending Apify run bookkeeping (async fallback for the 60s sync-poll
# window — see prospector/apify_client.py) ------------------------------

def get_pending_apify_runs(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """All pending Apify run entries stashed against this run, each a dict
    with at least {apify_run_id, kind ("fb"|"google"), business_id}, plus
    `domain` for "google" entries."""
    row = conn.execute("SELECT pending_apify_runs FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row or not row["pending_apify_runs"]:
        return []
    return json.loads(row["pending_apify_runs"])


def set_pending_apify_runs(conn: sqlite3.Connection, run_id: int, entries: list[dict]) -> None:
    conn.execute(
        "UPDATE runs SET pending_apify_runs = ? WHERE id = ?",
        (json.dumps(entries), run_id),
    )


def add_pending_apify_run(conn: sqlite3.Connection, run_id: int, entry: dict) -> None:
    entries = get_pending_apify_runs(conn, run_id)
    entries.append(entry)
    set_pending_apify_runs(conn, run_id, entries)


def update_business_ad_fields(conn: sqlite3.Connection, business_id: int, fields: dict) -> None:
    """Patch a subset of columns on a businesses row — used when a pending
    Apify run is collected later and we learn the real ad-active values."""
    if not fields:
        return
    cols = list(fields.keys())
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    conn.execute(
        f"UPDATE businesses SET {set_clause} WHERE id = ?",
        [fields[c] for c in cols] + [business_id],
    )


def get_business_for_rescore(conn: sqlite3.Connection, business_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, fb_ads_active, google_ads_active, is_group_owned, review_count "
        "FROM businesses WHERE id = ?",
        (business_id,),
    ).fetchone()


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
