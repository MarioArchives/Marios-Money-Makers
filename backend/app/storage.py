"""SQLite store for bars, summaries, fetch_log stamps and `meta`; see README.md.
Timestamps are ``YYYY-MM-DDTHH:MM:SSZ`` so string order is chronological.
``config.DB_PATH`` is read at call time so tests can monkeypatch it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3  # noqa: F401 - used by the implementation
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app import config  # noqa: F401 - DB_PATH read at call time
from app.alpaca_client import Bar, MarketClock

# Tier names; the table for a tier is f"bars_{tier}".
TIERS = ("minute", "hour", "days")

# Pseudo-(tier, ticker) under which the leaderboard's whole-universe
# snapshots fetch is stamped in `fetch_log`.
SUMMARIES_TIER = "summaries"
SUMMARIES_KEY = "*"

# `meta` key recording which Alpaca `adjustment` mode the stored bars carry.
BARS_ADJUSTMENT_META_KEY = "bars_adjustment"

# `meta` key under which the latest Alpaca market clock is cached, as one
# JSON blob (see StoredClock).
MARKET_CLOCK_META_KEY = "market_clock"

# Every tier accepted by the fetch_log helpers.
_FETCH_TIERS = (*TIERS, SUMMARIES_TIER)


@dataclass(frozen=True)
class StoredClock:
    """The `meta` table's cached Alpaca market clock, plus when it was
    fetched."""

    timestamp: str
    is_open: bool
    next_open: str
    next_close: str
    fetched_at: str


def set_market_clock(clock: MarketClock, fetched_at: str) -> None:
    """Upsert the cached market clock (`meta[MARKET_CLOCK_META_KEY]`) as one
    JSON blob, overwriting whatever was stored before."""
    set_meta(
        MARKET_CLOCK_META_KEY,
        json.dumps(
            {
                "timestamp": clock.timestamp,
                "is_open": clock.is_open,
                "next_open": clock.next_open,
                "next_close": clock.next_close,
                "fetched_at": fetched_at,
            }
        ),
    )


def get_market_clock() -> StoredClock | None:
    """Return the cached market clock, or None (never a raised exception)
    if missing, corrupt JSON, or a field is missing/wrong-typed."""
    raw = get_meta(MARKET_CLOCK_META_KEY)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None

    is_open = value.get("is_open")
    if "is_open" not in value or not isinstance(is_open, bool):
        return None

    strings: dict[str, str] = {}
    for key in ("timestamp", "next_open", "next_close", "fetched_at"):
        field_value = value.get(key)
        if key not in value or not isinstance(field_value, str):
            return None
        strings[key] = field_value

    return StoredClock(
        timestamp=strings["timestamp"],
        is_open=is_open,
        next_open=strings["next_open"],
        next_close=strings["next_close"],
        fetched_at=strings["fetched_at"],
    )


logger = logging.getLogger("app.storage")


@dataclass(frozen=True)
class SummaryRow:
    """One row of the ``summaries`` table; ``fetched_at`` is ignored on
    write (``upsert_summaries``'s ``fetched_at`` arg stamps the batch)."""

    ticker: str
    price: float | None
    previous_close: float | None
    currency: str
    error: str | None
    fetched_at: str = ""


def _table(tier: str) -> str:
    """Return the table name for ``tier``, validating it against TIERS."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}")
    return f"bars_{tier}"


def _create_table_sql(table: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "ticker TEXT NOT NULL, "
        "price REAL NOT NULL, "
        "analytics TEXT NOT NULL, "
        "ts TEXT NOT NULL, "
        "recorded_at TEXT NOT NULL, "
        "PRIMARY KEY (ticker, ts)"
        ")"
    )


FETCH_LOG_TABLE = "fetch_log"
_CREATE_FETCH_LOG_SQL = (
    f"CREATE TABLE IF NOT EXISTS {FETCH_LOG_TABLE} ("
    "tier TEXT NOT NULL, "
    "ticker TEXT NOT NULL, "
    "fetched_at TEXT NOT NULL, "
    "PRIMARY KEY (tier, ticker)"
    ")"
)


SUMMARIES_TABLE = "summaries"
_CREATE_SUMMARIES_SQL = (
    f"CREATE TABLE IF NOT EXISTS {SUMMARIES_TABLE} ("
    "ticker TEXT PRIMARY KEY, "
    "price REAL, "
    "previous_close REAL, "
    "currency TEXT NOT NULL, "
    "error TEXT, "
    "fetched_at TEXT NOT NULL"
    ")"
)

# Legacy cross-process fetch-lease table; no longer created, dropped on sight.
_LEGACY_FETCH_CLAIMS_TABLE = "fetch_claims"


META_TABLE = "meta"
_CREATE_META_SQL = (
    f"CREATE TABLE IF NOT EXISTS {META_TABLE} ("
    "key TEXT PRIMARY KEY, "
    "value TEXT"
    ")"
)


def _validate_fetch_tier(tier: str) -> None:
    """Accept the three bar tiers plus the ``summaries`` pseudo-tier."""
    if tier not in _FETCH_TIERS:
        raise ValueError(f"unknown tier: {tier!r}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _drop_legacy_fetch_claims_table(conn: sqlite3.Connection) -> None:
    """Drop a legacy ``fetch_claims`` table if present; no-op otherwise.
    Does not commit; the caller does."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (_LEGACY_FETCH_CLAIMS_TABLE,),
    ).fetchone()
    if exists:
        conn.execute(f"DROP TABLE {_LEGACY_FETCH_CLAIMS_TABLE}")
        logger.info(
            "storage: dropped legacy %r table (the cross-process fetch "
            "lease was removed; claims were ephemeral, so nothing is lost)",
            _LEGACY_FETCH_CLAIMS_TABLE,
        )


def init_db() -> None:
    """Create the DB file's parent directory and every table; idempotent."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        _drop_legacy_fetch_claims_table(conn)
        for tier in TIERS:
            conn.execute(_create_table_sql(_table(tier)))
        conn.execute(_CREATE_FETCH_LOG_SQL)
        conn.execute(_CREATE_SUMMARIES_SQL)
        conn.execute(_CREATE_META_SQL)
        conn.commit()


# --- meta (DB-level key/value bookkeeping) ----------------------------------


def get_meta(key: str) -> str | None:
    """Return the ``meta`` value stored under ``key``, or None if absent."""
    if not os.path.exists(config.DB_PATH):
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT value FROM {META_TABLE} WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def set_meta(key: str, value: str) -> None:
    """Upsert ``meta[key] = value``."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(_CREATE_META_SQL)
        conn.execute(
            f"INSERT INTO {META_TABLE} (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def _any_bars_stored(conn: sqlite3.Connection) -> bool:
    for tier in TIERS:
        row = conn.execute(f"SELECT 1 FROM {_table(tier)} LIMIT 1").fetchone()
        if row is not None:
            return True
    return False


def ensure_bars_adjustment(adjustment: str) -> bool:
    """If ``adjustment`` differs from the stored ``bars_adjustment`` meta value,
    clear the three bar tiers' ``fetch_log`` stamps (not the rows) so they
    refetch, then record the new mode; runs once per change. Call after :func:`init_db`."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        for tier in TIERS:
            conn.execute(_create_table_sql(_table(tier)))
        conn.execute(_CREATE_FETCH_LOG_SQL)
        conn.execute(_CREATE_META_SQL)
        row = conn.execute(
            f"SELECT value FROM {META_TABLE} WHERE key = ?",
            (BARS_ADJUSTMENT_META_KEY,),
        ).fetchone()
        stored = row[0] if row else None

        if stored == adjustment:
            return False

        invalidate = stored is not None or _any_bars_stored(conn)
        if invalidate:
            placeholders = ",".join("?" for _ in TIERS)
            cursor = conn.execute(
                f"DELETE FROM {FETCH_LOG_TABLE} WHERE tier IN ({placeholders})",
                TIERS,
            )
            logger.info(
                "storage: bars adjustment changed %r -> %r; cleared %d bar "
                "fetch_log stamps so stored bars get refetched",
                stored,
                adjustment,
                cursor.rowcount,
            )
        conn.execute(
            f"INSERT INTO {META_TABLE} (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (BARS_ADJUSTMENT_META_KEY, adjustment),
        )
        conn.commit()
    return invalidate


def upsert_bars(tier: str, ticker: str, bars: list[Bar], recorded_at: str) -> None:
    """Upsert ``bars`` on the ``(ticker, ts)`` PK; ``price``/``analytics``
    always replaced, but ``recorded_at`` only moves when ``price`` actually
    changed from what is stored."""
    table = _table(tier)
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(_create_table_sql(table))
        conn.executemany(
            f"INSERT INTO {table} (ticker, price, analytics, ts, recorded_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, ts) DO UPDATE SET "
            "price=excluded.price, "
            "analytics=excluded.analytics, "
            "recorded_at = CASE WHEN "
            f"{table}.price IS excluded.price "
            f"THEN {table}.recorded_at ELSE excluded.recorded_at END",
            [(ticker, bar.price, bar.analytics, bar.ts, recorded_at) for bar in bars],
        )
        conn.commit()


def get_bars(tier: str, ticker: str, since: str) -> list[tuple[str, float]]:
    """Return ``(ts, price)`` rows for ``ticker`` with ``ts >= since``, ordered by ``ts``."""
    table = _table(tier)
    if not os.path.exists(config.DB_PATH):
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT ts, price FROM {table} WHERE ticker = ? AND ts >= ? "
                "ORDER BY ts",
                (ticker, since),
            ).fetchall()
        return [(ts, price) for ts, price in rows]
    except sqlite3.OperationalError:
        return []


def get_stored_rows(tier: str, ticker: str) -> list[tuple[str, float, str, str]]:
    """Return every stored row for ``ticker`` in ``tier`` (no time window,
    for inspection) as ``(ts, price, analytics, recorded_at)``, ordered by ``ts``."""
    table = _table(tier)
    if not os.path.exists(config.DB_PATH):
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT ts, price, analytics, recorded_at FROM {table} "
                "WHERE ticker = ? ORDER BY ts",
                (ticker,),
            ).fetchall()
        return [tuple(row) for row in rows]
    except sqlite3.OperationalError:
        return []


def row_counts(ticker: str) -> dict[str, int]:
    """Return ``{tier: stored row count}`` for ``ticker``; every tier is
    present (0 when missing/empty)."""
    counts = {tier: 0 for tier in TIERS}
    if not os.path.exists(config.DB_PATH):
        return counts
    for tier in TIERS:
        try:
            with _connect() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {_table(tier)} WHERE ticker = ?",
                    (ticker,),
                ).fetchone()
            counts[tier] = int(row[0]) if row else 0
        except sqlite3.OperationalError:
            counts[tier] = 0
    return counts


def latest_recorded_at(tier: str, ticker: str) -> str | None:
    """Return ``MAX(recorded_at)`` for ``ticker`` in ``tier``, or None if no rows."""
    table = _table(tier)
    if not os.path.exists(config.DB_PATH):
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT MAX(recorded_at) FROM {table} WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def oldest_ts(tier: str, ticker: str) -> str | None:
    """Return ``MIN(ts)`` for ``ticker`` in ``tier``, or None if no rows.

    Used to widen the daily tier's Alpaca fetch back to the oldest stored bar.
    """
    table = _table(tier)  # validates tier even when the DB file is missing
    if not os.path.exists(config.DB_PATH):
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT MIN(ts) FROM {table} WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def record_fetch(tier: str, ticker: str, fetched_at: str) -> None:
    """Upsert ``(tier, ticker)``'s ``fetch_log`` row; called after every
    successful ``fetch_bars``, including a legitimately empty one."""
    _validate_fetch_tier(tier)
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(_CREATE_FETCH_LOG_SQL)
        conn.execute(
            f"INSERT INTO {FETCH_LOG_TABLE} (tier, ticker, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tier, ticker) DO UPDATE SET fetched_at=excluded.fetched_at",
            (tier, ticker, fetched_at),
        )
        conn.commit()


def last_fetch_at(tier: str, ticker: str) -> str | None:
    """Return when ``(tier, ticker)`` was last successfully fetched, or None."""
    _validate_fetch_tier(tier)
    if not os.path.exists(config.DB_PATH):
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT fetched_at FROM {FETCH_LOG_TABLE} WHERE tier = ? AND ticker = ?",
                (tier, ticker),
            ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def prune(now: datetime) -> None:
    """Drop minute rows older than 24h and hour rows older than 30d (strictly
    older; ``bars_days`` is never touched)."""
    if not os.path.exists(config.DB_PATH):
        return
    minute_cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hour_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with _connect() as conn:
            conn.execute(
                f"DELETE FROM {_table('minute')} WHERE ts < ?", (minute_cutoff,)
            )
            conn.execute(f"DELETE FROM {_table('hour')} WHERE ts < ?", (hour_cutoff,))
            conn.commit()
    except sqlite3.OperationalError:
        return


def latest_prices(tickers: list[str]) -> dict[str, tuple[str, float, str]]:
    """Return each ticker's newest ``bars_minute`` row as ``(ts, price,
    recorded_at)``; tickers with no stored rows are absent."""
    if not tickers or not os.path.exists(config.DB_PATH):
        return {}
    table = _table("minute")
    result: dict[str, tuple[str, float, str]] = {}
    try:
        with _connect() as conn:
            placeholders = ",".join("?" for _ in tickers)
            rows = conn.execute(
                f"SELECT ticker, ts, price, recorded_at FROM {table} "
                f"WHERE ticker IN ({placeholders}) "
                "ORDER BY ticker, ts",
                tickers,
            ).fetchall()
        for ticker, ts, price, recorded_at in rows:
            result[ticker] = (ts, price, recorded_at)
        return result
    except sqlite3.OperationalError:
        return {}


# --- summaries (leaderboard current state) ----------------------------------


def upsert_summaries(rows: list[SummaryRow], fetched_at: str) -> None:
    """Write a whole leaderboard batch + its ``fetch_log`` stamp atomically
    in one ``BEGIN IMMEDIATE`` transaction; both upserts are monotonic on
    ``fetched_at``, so a late/duplicate writer is a no-op."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(_CREATE_SUMMARIES_SQL)
        conn.execute(_CREATE_FETCH_LOG_SQL)
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            f"INSERT INTO {SUMMARIES_TABLE} "
            "(ticker, price, previous_close, currency, error, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "price=excluded.price, "
            "previous_close=excluded.previous_close, "
            "currency=excluded.currency, "
            "error=excluded.error, "
            "fetched_at=excluded.fetched_at "
            f"WHERE excluded.fetched_at > {SUMMARIES_TABLE}.fetched_at",
            [
                (row.ticker, row.price, row.previous_close, row.currency, row.error, fetched_at)
                for row in rows
            ],
        )
        conn.execute(
            f"INSERT INTO {FETCH_LOG_TABLE} (tier, ticker, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tier, ticker) DO UPDATE SET fetched_at=excluded.fetched_at "
            f"WHERE excluded.fetched_at > {FETCH_LOG_TABLE}.fetched_at",
            (SUMMARIES_TIER, SUMMARIES_KEY, fetched_at),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_summaries() -> dict[str, SummaryRow]:
    """Return every stored summary row keyed by ticker (``{}`` if none)."""
    if not os.path.exists(config.DB_PATH):
        return {}
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT ticker, price, previous_close, currency, error, fetched_at "
                f"FROM {SUMMARIES_TABLE}"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        ticker: SummaryRow(
            ticker=ticker,
            price=price,
            previous_close=previous_close,
            currency=currency,
            error=error,
            fetched_at=fetched_at,
        )
        for ticker, price, previous_close, currency, error, fetched_at in rows
    }

