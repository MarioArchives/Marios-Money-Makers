"""SQLite backup store for fetched market data.

Three tables — ``bars_minute``, ``bars_hour``, ``bars_month`` — with an
identical schema, one per retention tier:

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS bars_<tier> (
        ticker      TEXT NOT NULL,   -- stock name (ticker symbol)
        price       REAL NOT NULL,   -- bar close
        analytics   TEXT NOT NULL,   -- JSON: {"o","h","l","c","v","vw","n"}
        ts          TEXT NOT NULL,   -- bar time, ISO-8601 UTC ("...Z")
        recorded_at TEXT NOT NULL,   -- when we fetched it, ISO-8601 UTC
        PRIMARY KEY (ticker, ts)
    );

The composite primary key is the upsert target (re-fetching an overlapping
window must never duplicate rows) and covers the ``(ticker, ts)`` range
scans; no other index is needed at this scale.

A fourth table, ``fetch_log``, records when each ``(tier, ticker)`` was
last *successfully fetched from Alpaca* (one row per pair, upserted):

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS fetch_log (
        tier       TEXT NOT NULL,
        ticker     TEXT NOT NULL,
        fetched_at TEXT NOT NULL,   -- ISO-8601 UTC
        PRIMARY KEY (tier, ticker)
    );

It is what the history endpoint's freshness check reads. Deriving
freshness from the bar rows' ``recorded_at`` would be wrong for the minute
tier: the leaderboard poll upserts each ticker's latest minute bar every
~20s, which would make the tier look perpetually fresh and suppress the
intraday backfill. A legitimately-empty fetch also stores no bars but is
still a fetch, so it is logged here too.

Retention: ``prune(now)`` deletes ``bars_minute`` rows whose ``ts`` is
older than 24 hours and ``bars_hour`` rows older than 30 days (strictly
older: a row exactly at the cutoff survives). ``bars_month`` is never
pruned -- it backs the all-time history view and only ever grows.

``get_stored_rows`` / ``row_counts`` expose the raw table contents (every
column, no time window) for the ``/stored`` inspection endpoint.

All timestamps are stored as ``YYYY-MM-DDTHH:MM:SSZ`` strings, so
lexicographic comparison (used in every WHERE clause) equals chronological
comparison.

Connections are opened per call against ``app.config.DB_PATH`` — read via
the ``config`` module attribute at call time, never imported directly, so
tests can monkeypatch that single attribute to point at a tmp DB. WAL
journal mode is enabled. Calls are tiny (≤ ~1000 rows) and run
synchronously inside request handlers.
"""

from __future__ import annotations

import os
import sqlite3  # noqa: F401 - used by the implementation
from datetime import datetime, timedelta
from pathlib import Path

from app import config  # noqa: F401 - DB_PATH read at call time
from app.alpaca_client import Bar

# Tier names; the table for a tier is f"bars_{tier}".
TIERS = ("minute", "hour", "month")


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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create the DB file's parent directory and all three tables.

    Idempotent (``CREATE TABLE IF NOT EXISTS``); called from the app's
    lifespan hook and defensively before writes.
    """
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        for tier in TIERS:
            conn.execute(_create_table_sql(_table(tier)))
        conn.execute(_CREATE_FETCH_LOG_SQL)
        conn.commit()


def upsert_bars(tier: str, ticker: str, bars: list[Bar], recorded_at: str) -> None:
    """Insert ``bars`` for ``ticker``, updating on ``(ticker, ts)`` conflict.

    A conflicting row gets its ``price``, ``analytics`` and ``recorded_at``
    replaced (the current month's bar keeps mutating until month end).
    """
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
            "recorded_at=excluded.recorded_at",
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
    """Return every stored row for ``ticker`` in ``tier`` as
    ``(ts, price, analytics, recorded_at)``, ordered by ``ts``.

    No time window: this is the raw table contents, for inspection.
    """
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
    """Return ``{tier: stored row count}`` for ``ticker`` across all tiers.

    Every tier is present in the result (0 when the table is missing or
    empty), so callers can render a full overview without special cases.
    """
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


def record_fetch(tier: str, ticker: str, fetched_at: str) -> None:
    """Record that ``(tier, ticker)`` was successfully fetched at ``fetched_at``.

    Upserts the single row per pair. Called after every successful
    ``fetch_bars`` -- including a legitimately empty one.
    """
    _table(tier)  # validate tier
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
    _table(tier)  # validate tier
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
    """Apply retention: drop minute rows older than 24h and hour rows older than 30d.

    Rows exactly at the cutoff are kept; ``bars_month`` is never touched.
    """
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
    """Return each ticker's newest ``bars_minute`` row as ``(ts, price, recorded_at)``.

    Tickers with no stored minute rows are absent from the result. Backs
    the leaderboard's DB fallback when Alpaca is down and the in-memory
    cache is cold.
    """
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
