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


# v3 — Phase 2 of the Prospector v2 rebuild ("Discovery module"): adds
# Companies House incorporation/status enrichment columns and a scraped
# email column to `businesses`, all additive (nullable, default NULL/0) so
# the 8 pre-existing runs / 291 businesses are untouched. Backed up to
# prospector.db.bak-<timestamp>-phase2 before this migration ran.
_V3_ADD_BUSINESS_COLUMNS = [
    ("incorporation_date", "TEXT"),
    ("company_status", "TEXT"),
    ("established_flag", "INTEGER DEFAULT 0"),  # 1 if company is 3+ years old
    ("email", "TEXT"),
]

# v4 — Phase 3 ("Review profile module"): review-target scoring columns on
# `businesses`, plus a keyword-match column on `reviews` for missed-call
# evidence provenance. Additive/nullable.
_V4_ADD_BUSINESS_COLUMNS = [
    ("worst_recent_rating", "INTEGER"),
    ("has_negative_recent", "INTEGER DEFAULT 0"),
    ("review_target_score", "INTEGER"),
    ("weak_gbp", "INTEGER DEFAULT 0"),
    ("missed_call_evidence", "INTEGER DEFAULT 0"),
    ("reviews_fetched_at", "TEXT"),
]
_V4_ADD_REVIEW_COLUMNS = [
    ("review_keyword_match", "INTEGER DEFAULT 0"),
]

# v5 — Phase 4 ("Website signal module"): opportunity-score flags from the
# lightweight homepage/contact-page fetch. Additive/nullable.
_V5_ADD_BUSINESS_COLUMNS = [
    ("no_booking", "INTEGER DEFAULT 0"),
    ("no_chat", "INTEGER DEFAULT 0"),
    ("phone_dependent", "INTEGER DEFAULT 0"),
    ("opportunity_score", "INTEGER DEFAULT 0"),
    ("site_checked_at", "TEXT"),
]

# v6 — Phase 5 ("Combined targeting and export"): PECR/TPS compliance
# placeholder column. Defaults to 0 (false) — no TPS-checking API
# integration, just a column + README note per Andy's spec.
_V6_ADD_BUSINESS_COLUMNS = [
    ("tps_checked", "INTEGER DEFAULT 0"),
]

# v7 — Chain/franchise/corporate exclusion rule (post-rebuild standing
# request): flags businesses matching a known chain brand/domain, a
# corporate-entity Companies House PSC, or a multi-location domain/company
# number match (see prospector/chain_signals.py). Additive/nullable, same
# safe pattern as v3-v6 — no columns dropped, no rows touched.
# chain_reason is a free-text, semicolon-joined audit trail of *why* a
# business was flagged, so Andy can inspect (not silently lose) excluded
# businesses via `--include-chains`.
_V7_ADD_BUSINESS_COLUMNS = [
    ("is_chain", "INTEGER DEFAULT 0"),
    ("chain_reason", "TEXT"),
]


def _add_columns_if_missing(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, coltype in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def _migration_v3_ch_and_email(conn: sqlite3.Connection) -> None:
    _add_columns_if_missing(conn, "businesses", _V3_ADD_BUSINESS_COLUMNS)


def _migration_v4_review_scoring(conn: sqlite3.Connection) -> None:
    _add_columns_if_missing(conn, "businesses", _V4_ADD_BUSINESS_COLUMNS)
    _add_columns_if_missing(conn, "reviews", _V4_ADD_REVIEW_COLUMNS)


def _migration_v5_site_signals(conn: sqlite3.Connection) -> None:
    _add_columns_if_missing(conn, "businesses", _V5_ADD_BUSINESS_COLUMNS)


def _migration_v6_tps_placeholder(conn: sqlite3.Connection) -> None:
    _add_columns_if_missing(conn, "businesses", _V6_ADD_BUSINESS_COLUMNS)


def _migration_v7_chain_flag(conn: sqlite3.Connection) -> None:
    _add_columns_if_missing(conn, "businesses", _V7_ADD_BUSINESS_COLUMNS)


MIGRATIONS: list[tuple[int, "str | object"]] = [
    (1, "ALTER TABLE runs ADD COLUMN pending_apify_runs TEXT DEFAULT '[]';"),
    (2, _migration_v2_drop_ad_columns),
    (3, _migration_v3_ch_and_email),
    (4, _migration_v4_review_scoring),
    (5, _migration_v5_site_signals),
    (6, _migration_v6_tps_placeholder),
    (7, _migration_v7_chain_flag),
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
        "is_chain", "chain_reason",
        "priority", "priority_score", "created_at",
    ]
    values = [run_id] + [biz.get(c) for c in cols[1:-1]] + [_utcnow()]
    placeholders = ", ".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO businesses ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )
    return int(cur.lastrowid)


def normalize_phone(phone: str | None) -> str | None:
    """Strip everything but digits, for phone dedupe matching. '+44' and
    '0' UK prefixes are left as-is (digits only) — good enough for exact
    fallback-dedupe purposes; not a full E.164 normalizer."""
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits or None


def find_business_by_place_id(conn: sqlite3.Connection, place_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM businesses WHERE google_place_id = ? LIMIT 1", (place_id,)
    ).fetchone()


def find_business_by_phone_or_domain(conn: sqlite3.Connection, phone: str | None, domain: str | None) -> sqlite3.Row | None:
    """Fallback dedupe when place_id isn't a match (e.g. re-discovered via a
    different search). Matches on normalised phone digits or lower-cased
    domain, whichever is available."""
    norm_phone = normalize_phone(phone)
    if norm_phone:
        rows = conn.execute("SELECT * FROM businesses WHERE phone IS NOT NULL").fetchall()
        for row in rows:
            if normalize_phone(row["phone"]) == norm_phone:
                return row
    if domain:
        row = conn.execute(
            "SELECT * FROM businesses WHERE domain = ? LIMIT 1", (domain.lower(),)
        ).fetchone()
        if row:
            return row
    return None


def insert_discovered_business(conn: sqlite3.Connection, run_id: int, biz: dict) -> int:
    """Insert a business discovered via the Phase 2 discovery module
    (prospector/discovery/places.py). Distinct from insert_business (used by
    the legacy ad-hoc-scoring `prospector run` pipeline) only in that it also
    writes the Companies House enrichment columns added in migration v3 and
    doesn't require priority/priority_score (v2 businesses are scored later,
    in Phase 3/5)."""
    cols = [
        "run_id", "name", "vertical", "town", "postcode", "address", "website",
        "domain", "phone", "google_place_id", "rating", "review_count",
        "director_name", "companies_house_number", "is_group_owned",
        "incorporation_date", "company_status", "established_flag",
        "is_chain", "chain_reason",
        "created_at",
    ]
    values = [run_id] + [biz.get(c) for c in cols[1:-1]] + [_utcnow()]
    placeholders = ", ".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO businesses ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )
    return int(cur.lastrowid)


def count_businesses_sharing_domain(conn: sqlite3.Connection, domain: str | None, exclude_id: int | None = None) -> int:
    """How many *other* businesses already in the DB share this domain —
    the chain-signals "multi-location" check (prospector/chain_signals.py).
    Extends the existing place_id/phone/domain dedupe (db.py, Phase 2)
    rather than duplicating its matching logic; NULL/empty domains never
    match (avoids every no-website business falsely "sharing" a NULL)."""
    if not domain:
        return 0
    query = "SELECT COUNT(*) FROM businesses WHERE domain = ?"
    params: list = [domain.lower()]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    return int(conn.execute(query, params).fetchone()[0])


def count_businesses_sharing_company_number(conn: sqlite3.Connection, company_number: str | None, exclude_id: int | None = None) -> int:
    """How many *other* businesses already in the DB share this Companies
    House company number — same idea as count_businesses_sharing_domain,
    for firms that reuse one legal entity across branches even when each
    branch has its own website/domain."""
    if not company_number:
        return 0
    query = "SELECT COUNT(*) FROM businesses WHERE companies_house_number = ?"
    params: list = [company_number]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    return int(conn.execute(query, params).fetchone()[0])


def mark_chain_by_domain(conn: sqlite3.Connection, domain: str | None, reason: str, exclude_id: int | None = None) -> int:
    """Retroactively flag existing businesses sharing `domain` as
    is_chain=1 (appending `reason` to any existing chain_reason) — used
    when a *newly* discovered business reveals that an earlier-discovered
    sibling business is also part of the same chain, so both ends of a
    multi-location match get flagged, not just whichever was discovered
    second."""
    if not domain:
        return 0
    query = "SELECT id, chain_reason FROM businesses WHERE domain = ?"
    params: list = [domain.lower()]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    rows = conn.execute(query, params).fetchall()
    for row in rows:
        _append_chain_reason(conn, row["id"], row["chain_reason"], reason)
    return len(rows)


def mark_chain_by_company_number(conn: sqlite3.Connection, company_number: str | None, reason: str, exclude_id: int | None = None) -> int:
    """Companies-House-number counterpart to mark_chain_by_domain."""
    if not company_number:
        return 0
    query = "SELECT id, chain_reason FROM businesses WHERE companies_house_number = ?"
    params: list = [company_number]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    rows = conn.execute(query, params).fetchall()
    for row in rows:
        _append_chain_reason(conn, row["id"], row["chain_reason"], reason)
    return len(rows)


def _append_chain_reason(conn: sqlite3.Connection, business_id: int, existing_reason: str | None, new_reason: str) -> None:
    if existing_reason and new_reason in existing_reason:
        conn.execute("UPDATE businesses SET is_chain = 1 WHERE id = ?", (business_id,))
        return
    combined = f"{existing_reason}; {new_reason}" if existing_reason else new_reason
    conn.execute(
        "UPDATE businesses SET is_chain = 1, chain_reason = ? WHERE id = ?",
        (combined, business_id),
    )


def insert_review(conn: sqlite3.Connection, business_id: int, review: dict) -> int:
    cur = conn.execute(
        "INSERT INTO reviews (business_id, rating, text, review_date, pain_flag, review_keyword_match) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            business_id,
            review.get("rating"),
            review.get("text"),
            review.get("review_date"),
            1 if review.get("pain_flag") else 0,
            1 if review.get("review_keyword_match") else 0,
        ),
    )
    return int(cur.lastrowid)


def update_business_fields(conn: sqlite3.Connection, business_id: int, fields: dict) -> None:
    """Generic partial-update helper, used by enrichers/reviews.py (Phase 3)
    and enrichers/site.py (Phase 4) to write their scoring columns without
    each needing its own bespoke UPDATE statement."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE businesses SET {set_clause} WHERE id = ?",
        [*fields.values(), business_id],
    )


def businesses_needing_review_fetch(
    conn: sqlite3.Connection, run_id: int | None = None, business_id: int | None = None, refresh: bool = False
) -> list[sqlite3.Row]:
    """Businesses with a google_place_id, optionally filtered to one run or
    one business, that haven't had reviews_fetched_at set yet (unless
    refresh=True, which re-fetches everything matching the filter)."""
    query = "SELECT * FROM businesses WHERE google_place_id IS NOT NULL"
    params: list = []
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    if business_id is not None:
        query += " AND id = ?"
        params.append(business_id)
    if not refresh:
        query += " AND reviews_fetched_at IS NULL"
    return conn.execute(query, params).fetchall()


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
