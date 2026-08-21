"""SQLite store for fetched market data: history bars, the leaderboard's
current summaries, fetch stamps and fetch leases.

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
still a fetch, so it is logged here too. The leaderboard's snapshots
fetch is stamped in the same table under the pseudo-pair
``(tier='summaries', ticker='*')`` (:data:`SUMMARIES_TIER` /
:data:`SUMMARIES_KEY`).

A fifth table, ``summaries``, is the leaderboard's CURRENT-STATE store:
exactly one row per ticker (20 rows, ever -- no history, no pruning),
holding what the last successful ``GET /v2/stocks/snapshots`` returned:

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS summaries (
        ticker         TEXT PRIMARY KEY,
        price          REAL,            -- NULL when the row is an error entry
        previous_close REAL,            -- NULL when Alpaca had no prevDailyBar
        currency       TEXT NOT NULL,
        error          TEXT,            -- per-ticker error, NULL when good
        fetched_at     TEXT NOT NULL    -- batch stamp, ISO-8601 UTC
    );

``change`` / ``change_percent`` are derived on read, not stored.
``upsert_summaries`` writes all rows AND the ``fetch_log`` stamp in one
``BEGIN IMMEDIATE`` transaction, and both upserts are *monotonic*
(``... WHERE excluded.fetched_at > <table>.fetched_at``): a late writer
holding an older batch than what is stored is ignored, and re-writing the
same batch is a no-op. That is what makes the table safe to update from
several processes sharing one DB file.

A sixth table, ``fetch_claims``, is a lease used for cross-process single
flight (``try_claim`` / ``release_claim``): before fetching a
``(tier, ticker)`` from Alpaca a worker inserts a claim row; a competing
worker's insert conflicts and only succeeds if the existing claim is older
than the lease, so at most one process fetches a given pair per lease
window. The claim is deleted as soon as the fetch finishes.

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS fetch_claims (
        tier       TEXT NOT NULL,
        ticker     TEXT NOT NULL,
        claimed_at TEXT NOT NULL,   -- ISO-8601 UTC
        PRIMARY KEY (tier, ticker)
    );

A seventh table, ``meta``, is a tiny key/value store for DB-level
bookkeeping (``get_meta`` / ``set_meta``):

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

Its one current key is ``bars_adjustment``: the Alpaca ``adjustment``
mode (``raw|split|dividend|all``) the stored bars were fetched with.
``ensure_bars_adjustment(adjustment)`` (called from the app lifespan right
after ``init_db``) compares it with the configured mode and, when they
differ -- or the key is missing while any bars table already has rows,
i.e. a DB written before the key existed -- deletes the ``fetch_log``
stamps of the three bar tiers (NOT the bar rows, NOT the ``summaries``
stamp) and records the new mode. Stamp-less tiers are stale, so the next
chart request / backfill sweep refetches them and ``upsert_bars``
overwrites the old rows in place. This runs exactly once per change; a
fresh empty DB just records the mode.

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

import logging
import os
import sqlite3  # noqa: F401 - used by the implementation
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config  # noqa: F401 - DB_PATH read at call time
from app.alpaca_client import Bar

# Tier names; the table for a tier is f"bars_{tier}".
TIERS = ("minute", "hour", "month")

# Pseudo-(tier, ticker) under which the leaderboard's whole-universe
# snapshots fetch is stamped in `fetch_log` and leased in `fetch_claims`.
SUMMARIES_TIER = "summaries"
SUMMARIES_KEY = "*"

# Every tier accepted by the fetch_log / fetch_claims helpers.
_FETCH_TIERS = (*TIERS, SUMMARIES_TIER)

# `meta` key recording which Alpaca `adjustment` mode the stored bars carry.
BARS_ADJUSTMENT_META_KEY = "bars_adjustment"

logger = logging.getLogger("app.storage")


@dataclass(frozen=True)
class SummaryRow:
    """One row of the ``summaries`` table.

    ``fetched_at`` is the batch stamp the row was written with (filled in
    on read; ignored on write, where ``upsert_summaries``'s ``fetched_at``
    argument stamps the whole batch).
    """

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

FETCH_CLAIMS_TABLE = "fetch_claims"
_CREATE_FETCH_CLAIMS_SQL = (
    f"CREATE TABLE IF NOT EXISTS {FETCH_CLAIMS_TABLE} ("
    "tier TEXT NOT NULL, "
    "ticker TEXT NOT NULL, "
    "claimed_at TEXT NOT NULL, "
    "PRIMARY KEY (tier, ticker)"
    ")"
)


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


def init_db() -> None:
    """Create the DB file's parent directory and every table.

    Idempotent (``CREATE TABLE IF NOT EXISTS``); called from the app's
    lifespan hook and defensively before writes.
    """
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        for tier in TIERS:
            conn.execute(_create_table_sql(_table(tier)))
        conn.execute(_CREATE_FETCH_LOG_SQL)
        conn.execute(_CREATE_SUMMARIES_SQL)
        conn.execute(_CREATE_FETCH_CLAIMS_SQL)
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
    """Make the stored bars converge on Alpaca ``adjustment`` mode ``adjustment``.

    Compares the ``bars_adjustment`` meta value with ``adjustment``. When
    they differ -- or the key is missing while some bars table already has
    rows (a DB written before this key existed, i.e. fetched with Alpaca's
    default ``raw``) -- the ``fetch_log`` stamps of the three bar tiers
    are deleted so every pair is stale and gets refetched/overwritten by
    the next request or backfill sweep; the bar rows themselves and the
    ``summaries`` stamp are left alone. Then the meta key is set to
    ``adjustment``, so this happens once per change, not on every start.
    A fresh/empty DB just records the mode. Returns True if stamps were
    invalidated. Call after :func:`init_db`.
    """
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
    ``fetch_bars`` -- including a legitimately empty one. (The leaderboard
    stamp is written by ``upsert_summaries`` instead, atomically with its
    rows.)
    """
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

    Tickers with no stored minute rows are absent from the result.
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


# --- summaries (leaderboard current state) ----------------------------------


def upsert_summaries(rows: list[SummaryRow], fetched_at: str) -> None:
    """Write a whole leaderboard batch + its ``fetch_log`` stamp atomically.

    One ``BEGIN IMMEDIATE`` transaction covers every row of ``rows`` and the
    ``(SUMMARIES_TIER, SUMMARIES_KEY)`` stamp, so a reader never sees a
    half-written batch or a stamp without its rows. Both upserts are
    monotonic on ``fetched_at``: an existing row/stamp is only replaced
    when ``fetched_at`` is strictly newer than what is stored, so a late
    writer carrying an older batch changes nothing and a duplicate write
    is a no-op. ``fetched_at`` stamps every row (``row.fetched_at`` is
    ignored).
    """
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


# --- fetch leases (cross-process single flight) -----------------------------


def _minus_seconds(ts: str, seconds: float) -> str:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def try_claim(tier: str, ticker: str, now_iso: str, lease_seconds: float) -> bool:
    """Try to take the fetch lease for ``(tier, ticker)``; True if we hold it.

    A single upsert: insert the claim, or -- if one exists -- take it over
    only when it is older than ``lease_seconds`` (``claimed_at <
    now - lease``). Returns ``rowcount == 1``, i.e. whether *this* call
    inserted or refreshed the row. A live claim held by anyone (including a
    previous call of our own) yields ``False``.
    """
    _validate_fetch_tier(tier)
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    expired_before = _minus_seconds(now_iso, lease_seconds)
    with _connect() as conn:
        conn.execute(_CREATE_FETCH_CLAIMS_SQL)
        cursor = conn.execute(
            f"INSERT INTO {FETCH_CLAIMS_TABLE} (tier, ticker, claimed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tier, ticker) DO UPDATE SET claimed_at=excluded.claimed_at "
            f"WHERE {FETCH_CLAIMS_TABLE}.claimed_at < ?",
            (tier, ticker, now_iso, expired_before),
        )
        claimed = cursor.rowcount == 1
        conn.commit()
    return claimed


def release_claim(tier: str, ticker: str) -> None:
    """Drop the fetch lease for ``(tier, ticker)`` (no-op if absent)."""
    _validate_fetch_tier(tier)
    if not os.path.exists(config.DB_PATH):
        return
    try:
        with _connect() as conn:
            conn.execute(
                f"DELETE FROM {FETCH_CLAIMS_TABLE} WHERE tier = ? AND ticker = ?",
                (tier, ticker),
            )
            conn.commit()
    except sqlite3.OperationalError:
        return
