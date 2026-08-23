"""``/api/stocks`` routes: leaderboard, single-ticker summary, history, and
raw-store inspection. SQLite is the only cache (no in-memory layer); see
README.md for the DB-first/single-flight/backoff flow in full.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException

from app import alpaca_client, storage
from app.alpaca_client import Bar
from app.config import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    CACHE_TTL_SECONDS,
    FRESHNESS_HOUR_SECONDS,
    FRESHNESS_MINUTE_SECONDS,
    FRESHNESS_DAY_SECONDS,
    HOUR_RETENTION_DAYS,
    MINUTE_RETENTION_HOURS,
    DAY_BACKFILL_DAYS,
)
from app.schemas import (
    HistoryPoint,
    HistoryResponse,
    StockSummary,
    StocksResponse,
    StoredBar,
    StoredDataResponse,
)
from app.storage import SUMMARIES_KEY, SUMMARIES_TIER, SummaryRow
from app.tickers import TICKER_SYMBOLS, TICKERS_BY_SYMBOL

logger = logging.getLogger("app.routers.stocks")

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

# One asyncio.Lock per key in `_refresh_locks` (SUMMARIES_LOCK_KEY, or
# "{ticker}:{tier}") is the whole single-flight guarantee, in-process only.
SUMMARIES_LOCK_KEY = "summaries"
_refresh_locks: dict[str, asyncio.Lock] = {}

# Error text for a leaderboard row we have never managed to fetch.
NO_DATA_ERROR = f"{alpaca_client.ERROR_PREFIX}: no data yet"

# range query param -> (tier, Alpaca timeframe, fetch window, freshness
# seconds, response `interval` label).
_TIER_BY_RANGE: dict[str, tuple[str, str, timedelta, float, str]] = {
    "1d": (
        "minute",
        alpaca_client.TIMEFRAME_MINUTE,
        timedelta(hours=MINUTE_RETENTION_HOURS),
        FRESHNESS_MINUTE_SECONDS,
        "1m",
    ),
    "30d": (
        "hour",
        alpaca_client.TIMEFRAME_HOUR,
        timedelta(days=HOUR_RETENTION_DAYS),
        FRESHNESS_HOUR_SECONDS,
        "1h",
    ),
    "all": (
        "days",
        alpaca_client.TIMEFRAME_DAYS,
        timedelta(days=DAY_BACKFILL_DAYS),
        FRESHNESS_DAY_SECONDS,
        "1d",
    ),
}

# Same table keyed by tier, for callers that think in tiers rather than
# ranges (`refresh_history`, the backfill sweep).
_SPEC_BY_TIER: dict[str, tuple[str, timedelta, float]] = {
    tier: (timeframe, window, freshness)
    for tier, timeframe, window, freshness, _interval in _TIER_BY_RANGE.values()
}

HistoryRange = Literal["1d", "30d", "all"]

# Tiers whose read is unbounded (every stored row served); only "days"
# qualifies since it is never pruned. Also drives `_fetch_start` below.
_UNBOUNDED_READ_TIERS = frozenset({"days"})

StoredTier = Literal["minute", "hour", "days"]

# Test seam: module-level clocks so tests can substitute fakes.
_monotonic = time.monotonic


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_since(tier: str, window: timedelta, now: datetime) -> str:
    """Lower ``ts`` bound for reading ``tier``: ``""`` (every row) for the
    never-pruned daily tier, else ``now - window``."""
    if tier in _UNBOUNDED_READ_TIERS:
        return ""
    return _iso(now - window)


def _fetch_start(tier: str, ticker: str, window: timedelta, now: datetime) -> datetime:
    """Lower bound for the Alpaca fetch ``start``. For the never-pruned daily
    tier, widens back to ``storage.oldest_ts`` so an aged-out row is still
    re-checked; other tiers just get ``now - window``."""
    if tier not in _UNBOUNDED_READ_TIERS:
        return now - window
    oldest = storage.oldest_ts(tier, ticker)
    if oldest is None:
        return now - window
    oldest_dt = _parse_iso(oldest)
    if oldest_dt is None:
        return now - window
    return min(now - window, oldest_dt)


def _is_history_fresh(tier: str, ticker: str, freshness: float) -> bool:
    """True if the last successful Alpaca bars fetch for this pair is still
    fresh. Reads ``fetch_log``, not bar rows' ``recorded_at`` — the
    leaderboard's minute-bar writes would otherwise mask staleness."""
    fetched_at = storage.last_fetch_at(tier, ticker)
    if fetched_at is None:
        return False
    fetched_dt = _parse_iso(fetched_at)
    if fetched_dt is None:
        return False
    return (_utcnow() - fetched_dt).total_seconds() <= freshness


class _BackoffState:
    """In-process exponential-backoff bookkeeping for the batch fetch."""

    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.blocked_until = 0.0
        # Last all-error batch, kept so a cold start that has never had a
        # successful fetch still has something (200, error-flagged) to
        # serve while backing off.
        self.last_failed_batch: dict[str, StockSummary] | None = None

    def is_blocked(self) -> bool:
        return self.consecutive_failures > 0 and _monotonic() < self.blocked_until

    def record_failure(self, summaries: dict[str, StockSummary]) -> None:
        self.consecutive_failures += 1
        self.last_failed_batch = summaries
        delay = min(
            BACKOFF_BASE_SECONDS * (2 ** (self.consecutive_failures - 1)),
            BACKOFF_MAX_SECONDS,
        )
        self.blocked_until = _monotonic() + delay

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.blocked_until = 0.0
        self.last_failed_batch = None


_backoff = _BackoffState()


@router.get("", response_model=StocksResponse)
async def list_stocks() -> StocksResponse:
    """Return summaries for all 20 tickers in the fixed universe.

    Always 200s: per-ticker failures carry ``is_stale=True``/``error`` instead.
    """
    summaries = await _get_summaries_batch()
    return StocksResponse(
        updated_at=datetime.now(timezone.utc).isoformat(),
        stocks=[summaries[ticker] for ticker in TICKER_SYMBOLS],
    )


@router.get("/{ticker}", response_model=StockSummary)
async def get_stock(ticker: str) -> StockSummary:
    """Return the summary for a single ticker.

    404s if ``ticker`` is not one of the fixed 20 in
    :data:`app.tickers.TICKER_SYMBOLS`.
    """
    if ticker not in TICKERS_BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    summaries = await _get_summaries_batch()
    return summaries[ticker]


@router.get("/{ticker}/history", response_model=HistoryResponse)
async def get_stock_history(
    ticker: str, range: HistoryRange = "1d"
) -> HistoryResponse:
    """Return history points for one ticker at the tier matching ``range``.

    404 for an unknown ticker, 422 for a bad ``range``, 503 only when
    Alpaca is down and the DB holds nothing for this (ticker, tier).
    """
    if ticker not in TICKERS_BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    # Defensive: idempotent, in case the lifespan hook never ran (e.g. tests).
    storage.init_db()

    tier, timeframe, window, freshness, interval = _TIER_BY_RANGE[range]

    # Refresh-if-stale, then read; on Alpaca failure serve the DB stale.
    try:
        await refresh_history(ticker, tier)
    except alpaca_client.AlpacaError as exc:
        since = _read_since(tier, window, _utcnow())
        rows = storage.get_bars(tier, ticker, since=since)
        if rows:
            return HistoryResponse(
                ticker=ticker,
                interval=interval,
                range=range,
                points=[HistoryPoint(t=ts, close=price) for ts, price in rows],
                is_stale=True,
                error=str(exc),
            )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    since = _read_since(tier, window, _utcnow())
    rows = storage.get_bars(tier, ticker, since=since)
    return HistoryResponse(
        ticker=ticker,
        interval=interval,
        range=range,
        points=[HistoryPoint(t=ts, close=price) for ts, price in rows],
        is_stale=False,
        error=None,
    )


async def refresh_history(ticker: str, tier: str) -> bool:
    """Refresh one (ticker, tier) pair from Alpaca into SQLite if stale;
    the single write path shared by the history endpoint and the backfill
    sweep. Returns ``True`` if a fetch happened; :class:`AlpacaError` propagates."""
    if tier not in _SPEC_BY_TIER:
        raise ValueError(f"unknown tier: {tier!r}")
    timeframe, window, freshness = _SPEC_BY_TIER[tier]
    lock = _refresh_locks.setdefault(f"{ticker}:{tier}", asyncio.Lock())

    async with lock:
        # Double-checked: another task may have already refreshed this pair.
        if _is_history_fresh(tier, ticker, freshness):
            return False

        now = _utcnow()
        start = _fetch_start(tier, ticker, window, now)
        bars = await asyncio.to_thread(
            alpaca_client.fetch_bars, ticker, timeframe, start
        )

        recorded_at = _iso(now)
        storage.upsert_bars(tier, ticker, bars, recorded_at=recorded_at)
        storage.record_fetch(tier, ticker, _iso(_utcnow()))
        storage.prune(_utcnow())
        return True


def _parse_analytics(raw: str) -> dict:
    """Decode a stored ``analytics`` JSON string; never raises. Unreadable
    or non-object JSON is surfaced as ``{"raw": <string>}``, not hidden."""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": raw}
    if not isinstance(decoded, dict):
        return {"raw": raw}
    return decoded


@router.get("/{ticker}/stored", response_model=StoredDataResponse)
async def get_stored_data(ticker: str, tier: StoredTier = "minute") -> StoredDataResponse:
    """Return everything the SQLite store holds for ``ticker`` in ``tier``.

    Read-only, never calls Alpaca. 404 for an unknown ticker, 422 for an
    unknown tier; an empty tier is a 200 with ``rows: []``.
    """
    if ticker not in TICKERS_BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    rows = storage.get_stored_rows(tier, ticker)
    return StoredDataResponse(
        ticker=ticker,
        tier=tier,
        table=f"bars_{tier}",
        last_fetch_at=storage.last_fetch_at(tier, ticker),
        counts=storage.row_counts(ticker),
        rows=[
            StoredBar(
                ts=ts,
                price=price,
                analytics=_parse_analytics(analytics),
                recorded_at=recorded_at,
            )
            for ts, price, analytics, recorded_at in rows
        ],
    )


def _persist_minute_bars(minute_bars: dict[str, Bar], recorded_at: str) -> None:
    """Best-effort upsert of a summaries batch's minute bars + pruning;
    swallows every storage exception (the DB is a backup, not the source)."""
    try:
        for ticker, bar in minute_bars.items():
            storage.upsert_bars("minute", ticker, [bar], recorded_at)
        storage.prune(_utcnow())
    except Exception:
        pass


def _is_total_failure(summaries: dict[str, StockSummary]) -> bool:
    """True if every summary in a non-empty batch carries an error."""
    return bool(summaries) and all(s.error is not None for s in summaries.values())


def _is_summaries_fresh() -> bool:
    """True if the last successful snapshots fetch is within ``CACHE_TTL_SECONDS``
    (reads the ``fetch_log`` stamp ``upsert_summaries`` writes atomically)."""
    fetched_at = storage.last_fetch_at(SUMMARIES_TIER, SUMMARIES_KEY)
    if fetched_at is None:
        return False
    fetched_dt = _parse_iso(fetched_at)
    if fetched_dt is None:
        return False
    return (_utcnow() - fetched_dt).total_seconds() <= CACHE_TTL_SECONDS


def _summary_rows(summaries: dict[str, StockSummary]) -> list[SummaryRow]:
    """Project a fetched batch onto ``summaries`` table rows (change is not stored)."""
    return [
        SummaryRow(
            ticker=summary.ticker,
            price=summary.price,
            previous_close=summary.previous_close,
            currency=summary.currency,
            error=summary.error,
        )
        for summary in summaries.values()
    ]


def _no_data_summary(ticker: str) -> StockSummary:
    info = TICKERS_BY_SYMBOL[ticker]
    return StockSummary(
        ticker=ticker,
        name=info.name,
        sector=info.sector,
        price=None,
        currency=alpaca_client.USD,
        previous_close=None,
        change=None,
        change_percent=None,
        is_stale=True,
        error=NO_DATA_ERROR,
    )


def _batch_from_table(*, stale: bool) -> dict[str, StockSummary] | None:
    """Build the leaderboard batch from the ``summaries`` table, deriving
    ``change``/``change_percent`` on read; ``None`` if the table is empty."""
    rows = storage.get_summaries()
    if not rows:
        return None
    batch: dict[str, StockSummary] = {}
    for ticker in TICKER_SYMBOLS:
        row = rows.get(ticker)
        if row is None:
            batch[ticker] = _no_data_summary(ticker)
            continue
        info = TICKERS_BY_SYMBOL[ticker]
        change: float | None = None
        change_percent: float | None = None
        if row.price is not None and row.previous_close:
            change = row.price - row.previous_close
            change_percent = change / row.previous_close * 100
        batch[ticker] = StockSummary(
            ticker=ticker,
            name=info.name,
            sector=info.sector,
            price=row.price,
            currency=row.currency,
            previous_close=row.previous_close,
            change=change,
            change_percent=change_percent,
            is_stale=stale or row.error is not None,
            error=row.error,
        )
    return batch


def _stale_fallback() -> dict[str, StockSummary]:
    """Best batch available without touching Alpaca (never ``None``): the
    stale table, else the last all-error batch, else all-``NO_DATA_ERROR``."""
    table = _batch_from_table(stale=True)
    if table is not None:
        return table
    if _backoff.last_failed_batch is not None:
        return _backoff.last_failed_batch
    return {ticker: _no_data_summary(ticker) for ticker in TICKER_SYMBOLS}


async def _get_summaries_batch() -> dict[str, StockSummary]:
    """Return the leaderboard batch, refreshing the ``summaries`` table if
    stale, under single flight + backoff (see README.md). Never raises for
    Alpaca reasons; anything unexpected propagates after releasing the lock."""
    if _is_summaries_fresh():
        fresh = _batch_from_table(stale=False)
        if fresh is not None:
            return fresh
    if _backoff.is_blocked():
        return _stale_fallback()

    lock = _refresh_locks.setdefault(SUMMARIES_LOCK_KEY, asyncio.Lock())
    async with lock:
        # Double-checked: the leader we queued behind may have settled this.
        if _is_summaries_fresh():
            fresh = _batch_from_table(stale=False)
            if fresh is not None:
                return fresh
        if _backoff.is_blocked():
            return _stale_fallback()

        summaries, minute_bars = await asyncio.to_thread(
            alpaca_client.fetch_summaries, TICKER_SYMBOLS
        )
        if _is_total_failure(summaries):
            # Write nothing: the table keeps the last known-good rows.
            _backoff.record_failure(summaries)
            return _stale_fallback()

        fetched_at = _iso(_utcnow())
        try:
            storage.upsert_summaries(_summary_rows(summaries), fetched_at)
        except Exception:  # noqa: BLE001 - the DB is a backup; serve the data
            logger.exception("summaries: failed to store the fetched batch")
        _persist_minute_bars(minute_bars, fetched_at)
        _backoff.record_success()
        return summaries


async def refresh_summaries() -> None:
    """Refresh the ``summaries`` table if stale; thin wrapper around
    :func:`_get_summaries_batch` for callers that only want the side effect."""
    await _get_summaries_batch()
