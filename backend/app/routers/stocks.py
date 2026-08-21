"""``/api/stocks`` routes.

Three endpoints:

- ``GET /api/stocks`` — leaderboard: all 20 tickers' summaries. Backed by
  a single :class:`app.cache.TTLCacheWithLock` keyed on
  :data:`STOCKS_CACHE_KEY`, whose ``fetch`` invokes
  :func:`app.alpaca_client.fetch_summaries` for the full universe (one
  snapshots request). Never 500s on partial per-ticker failure — those
  entries simply carry ``is_stale=True``/``error``. Each successful batch
  also best-effort persists every ticker's latest minute bar into the
  SQLite ``bars_minute`` table (a DB hiccup must never fail the endpoint),
  so the DB backup covers the leaderboard too.
- ``GET /api/stocks/{ticker}`` — a single ticker's summary, sourced from
  the same cached batch. 404s for any ticker outside the fixed universe
  in :data:`app.tickers.TICKER_SYMBOLS`.
- ``GET /api/stocks/{ticker}/history?range=1d|30d|all`` — history points
  for one ticker at the tier matching ``range`` (``1d`` → minute bars,
  ``30d`` → hourly, ``all`` → daily). SQLite IS the cache here (there is
  no in-memory history cache): per (ticker, tier), if the last successful
  (possibly empty) Alpaca bars fetch — recorded in the ``fetch_log`` table,
  so it survives restarts and is NOT bumped by the leaderboard's minute-bar
  writes — is within the tier's freshness window, the DB is served with
  zero Alpaca calls. Otherwise Alpaca is fetched, rows upserted (+ retention
  pruning), and the merged window served. On Alpaca failure the DB
  contents are served with ``is_stale=True``; only when the DB holds
  nothing at all for that (ticker, tier) does this endpoint respond 503.
  404s for unknown tickers; an unknown ``range`` value 422s via the
  ``Literal`` annotation. Concurrent requests for the same (ticker, tier)
  collapse into one fetch under a per-key ``asyncio.Lock``
  (double-checked freshness after acquiring). The refresh itself lives in
  :func:`refresh_history` -- lock, re-check, ``fetch_bars`` in a worker
  thread, upsert, ``record_fetch``, prune -- which is also what
  :mod:`app.backfill`'s startup + periodic sweep calls for every
  (ticker, tier), so the bar tables stay populated even when nobody is
  viewing a chart; a request that lands on a pair the sweep has just
  refreshed is simply a fresh DB read.

  ``all`` is the all-time view: the daily tier is never pruned, so the
  response carries *every* stored daily bar for the ticker (no time
  window on the read); only the Alpaca fetch is bounded, backfilling
  ``MONTH_BACKFILL_DAYS`` on the first fetch. The series therefore grows
  by one bar per trading day for as long as the store lives.
- ``GET /api/stocks/{ticker}/stored?tier=minute|hour|month`` — raw
  inspection of the SQLite store for one ticker: every row of the tier's
  table (all columns, ``analytics`` JSON parsed, no time window, oldest
  first), the row count per tier and the tier's last successful Alpaca
  fetch time. Read-only: never calls Alpaca, never writes. 404 for
  unknown tickers, 422 for unknown tiers.

Rate-limit hardening (summaries)
--------------------------------
``fetch_summaries`` never raises — a fully rate-limited batch comes back
as 20 error-flagged summaries, which look like a *successful* fetch to
``get_or_fetch`` and would otherwise be cached, clobbering the stale
shadow store and blanking the page. So the batch fetch here:

1. Treats a batch in which *every* summary carries an ``error`` as a total
   failure and raises out of ``fetch``, so nothing is cached and the last
   known-good batch survives.
2. Serves, in order of preference: the last known-good in-memory batch
   (``is_stale=True``); a batch built from the newest SQLite minute rows
   (price set, change fields ``None``, ``is_stale=True``); the
   error-flagged batch itself (still 200 — the frontend renders those
   rows greyed).
3. Applies bounded exponential backoff between refresh attempts while
   total failures continue. Reset on the first successful batch.

"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException

from app import alpaca_client, storage
from app.alpaca_client import Bar
from app.cache import TTLCacheWithLock
from app.config import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    CACHE_TTL_SECONDS,
    FRESHNESS_HOUR_SECONDS,
    FRESHNESS_MINUTE_SECONDS,
    FRESHNESS_MONTH_SECONDS,
    HOUR_RETENTION_DAYS,
    MINUTE_RETENTION_HOURS,
    MONTH_BACKFILL_DAYS,
)
from app.schemas import (
    HistoryPoint,
    HistoryResponse,
    StockSummary,
    StocksResponse,
    StoredBar,
    StoredDataResponse,
)
from app.tickers import TICKER_SYMBOLS, TICKERS_BY_SYMBOL

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

# Single cache entry holding the whole 20-ticker batch, so
# GET /api/stocks and GET /api/stocks/{ticker} share one fetch.
STOCKS_CACHE_KEY = "stocks"
_stocks_cache: TTLCacheWithLock[dict[str, StockSummary]] = TTLCacheWithLock(
    ttl=CACHE_TTL_SECONDS
)

# History has NO in-memory cache: SQLite is the cache (bars tables +
# `fetch_log`). The only in-memory history state is `_history_locks`: a
# per-"{ticker}:{tier}" asyncio.Lock collapsing concurrent cache-miss
# fetches (what TTLCacheWithLock used to do).
_history_locks: dict[str, asyncio.Lock] = {}

# range query param -> (tier, Alpaca timeframe, fetch window, freshness
# seconds, response `interval` label). The fetch window bounds the Alpaca
# request (`start = now - window`); the *read* window is the same for the
# pruned tiers but unbounded for the never-pruned daily tier (see
# `_read_since`), which is what makes `all` grow over time.
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
        "month",
        alpaca_client.TIMEFRAME_MONTH,
        timedelta(days=MONTH_BACKFILL_DAYS),
        FRESHNESS_MONTH_SECONDS,
        # TIMEFRAME_MONTH == "1Day": the "month" tier's individual bars are
        # daily (it is never pruned, unlike the "hour" tier), so the
        # response's interval label is "1d", not "1mo".
        "1d",
    ),
}

# tier -> (Alpaca timeframe, fetch window, freshness seconds): the same
# table keyed by tier, for callers that think in tiers rather than ranges
# (`refresh_history`, the backfill sweep).
_SPEC_BY_TIER: dict[str, tuple[str, timedelta, float]] = {
    tier: (timeframe, window, freshness)
    for tier, timeframe, window, freshness, _interval in _TIER_BY_RANGE.values()
}

HistoryRange = Literal["1d", "30d", "all"]

# Tiers whose read is unbounded: every stored row is served. Only the daily
# tier qualifies (it is never pruned); the minute/hour tiers are read over
# their retention window, which `prune` keeps in step anyway.
_UNBOUNDED_READ_TIERS = frozenset({"month"})

StoredTier = Literal["minute", "hour", "month"]

# Clocks, indirected through module globals so tests can substitute fakes:
# `_monotonic` drives backoff windows, `_utcnow` drives freshness checks
# and `recorded_at` stamps.
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
    """Lower ``ts`` bound for reading ``tier`` from SQLite.

    ``""`` (every row, since all stored timestamps sort after the empty
    string) for the never-pruned daily tier; ``now - window`` otherwise.
    """
    if tier in _UNBOUNDED_READ_TIERS:
        return ""
    return _iso(now - window)


def _is_history_fresh(tier: str, ticker: str, freshness: float) -> bool:
    """True if the last successful Alpaca bars fetch for this pair is still fresh.

    Reads ``storage.last_fetch_at`` (the ``fetch_log`` table) rather than
    the bar rows' ``recorded_at``: the leaderboard poll upserts a minute
    bar per ticker every ~20s, which would otherwise keep the minute tier
    looking fresh forever and suppress the intraday backfill. A
    legitimately-empty fetch is logged too, so it still counts as fresh.
    """
    fetched_at = storage.last_fetch_at(tier, ticker)
    if fetched_at is None:
        return False
    fetched_dt = _parse_iso(fetched_at)
    if fetched_dt is None:
        return False
    return (_utcnow() - fetched_dt).total_seconds() <= freshness


class _TotalFetchFailure(Exception):
    """Every ticker in the batch failed; carries the error-flagged batch."""

    def __init__(self, summaries: dict[str, StockSummary]) -> None:
        super().__init__("all tickers failed to refresh")
        self.summaries = summaries


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

    Always 200s: per-ticker fetch failures are carried in the response as
    ``is_stale=True``/``error`` entries rather than failing the batch, and
    a total failure serves the best available fallback marked stale.
    """
    summaries = await _fetch_stocks_batch()
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

    summaries = await _fetch_stocks_batch()
    return summaries[ticker]


@router.get("/{ticker}/history", response_model=HistoryResponse)
async def get_stock_history(
    ticker: str, range: HistoryRange = "1d"
) -> HistoryResponse:
    """Return history points for one ticker at the tier matching ``range``.

    See the module docstring for the SQLite-first flow. 404s for unknown
    tickers; 422s (via the ``Literal`` annotation) for unknown ranges;
    503s only when Alpaca is down AND the DB holds nothing for this
    (ticker, tier).
    """
    if ticker not in TICKERS_BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    # Defensive: `init_db` normally runs once from the app's lifespan hook,
    # but is idempotent and cheap, so call it here too in case that hook
    # never ran (e.g. a test client that skips ASGI lifespan events). This
    # guarantees all three tier tables exist even when only one tier is
    # touched by this request.
    storage.init_db()

    tier, timeframe, window, freshness, interval = _TIER_BY_RANGE[range]

    # Refresh-if-stale under the per-(ticker, tier) lock (shared with the
    # backfill sweep), then read. On Alpaca failure the DB is still served
    # (stale); only an empty DB for this pair is a 503.
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
    """Refresh one (ticker, tier) pair from Alpaca into SQLite if it is stale.

    The single write path for history bars, shared by the history endpoint
    and :mod:`app.backfill`'s sweep. Acquires the pair's lock from
    ``_history_locks`` (collapsing concurrent refreshes into one fetch),
    re-checks ``fetch_log`` freshness inside the lock (returns ``False``
    with zero Alpaca calls if another task already refreshed), then runs
    :func:`app.alpaca_client.fetch_bars` in a worker thread -- it is a
    blocking httpx call, and must not stall the event loop for the other
    requests (or the sweep) -- and upserts the bars, stamps ``fetch_log``
    and prunes. Returns ``True`` when a fetch happened.

    :class:`app.alpaca_client.AlpacaError` propagates: the caller decides
    whether the DB can cover for the outage (the endpoint serves stale
    rows; the sweep logs and moves on). Nothing is written on failure, so
    ``fetch_log`` keeps marking the pair stale and the next caller retries.
    """
    if tier not in _SPEC_BY_TIER:
        raise ValueError(f"unknown tier: {tier!r}")
    timeframe, window, freshness = _SPEC_BY_TIER[tier]
    key = f"{ticker}:{tier}"
    lock = _history_locks.setdefault(key, asyncio.Lock())

    async with lock:
        # Double-checked freshness: another task racing us on the same
        # (ticker, tier) may have already refreshed while we waited for
        # the lock.
        if _is_history_fresh(tier, ticker, freshness):
            return False

        now = _utcnow()
        bars = await asyncio.to_thread(
            alpaca_client.fetch_bars, ticker, timeframe, now - window
        )

        recorded_at = _iso(now)
        storage.upsert_bars(tier, ticker, bars, recorded_at=recorded_at)
        storage.record_fetch(tier, ticker, _iso(_utcnow()))
        storage.prune(_utcnow())
        return True


def _parse_analytics(raw: str) -> dict:
    """Decode a stored ``analytics`` JSON string; never raises.

    A row whose JSON is unreadable (or not an object) is surfaced as
    ``{"raw": <string>}`` rather than hidden -- this endpoint exists to
    show exactly what is stored.
    """
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

    Read-only inspection of the backup store: every row of the tier's
    table with all columns (``analytics`` parsed back into an object),
    oldest first, plus per-tier row counts and the tier's last successful
    Alpaca fetch time from ``fetch_log``. Never calls Alpaca. 404s for
    unknown tickers; 422s (via the ``Literal`` annotation) for unknown
    tiers. An empty tier is a 200 with ``rows: []``, not an error.
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
    """Best-effort upsert of a summaries batch's minute bars + pruning.

    Swallows every storage exception: the DB is a backup, and a write
    failure must never fail the endpoint that just fetched good data.
    """
    try:
        for ticker, bar in minute_bars.items():
            storage.upsert_bars("minute", ticker, [bar], recorded_at)
        storage.prune(_utcnow())
    except Exception:
        pass


def _db_fallback_batch(
    failed: dict[str, StockSummary],
) -> dict[str, StockSummary] | None:
    """Build a leaderboard batch from the newest SQLite minute rows.

    Tickers with a stored row get ``price`` set (change fields ``None``,
    ``is_stale=True``, ``error=None``); tickers without one keep their
    entry from ``failed``. Returns ``None`` when no ticker has any stored
    row at all.
    """
    latest = storage.latest_prices(TICKER_SYMBOLS)
    if not latest:
        return None

    result: dict[str, StockSummary] = {}
    for ticker in TICKER_SYMBOLS:
        if ticker in latest:
            _, price, _ = latest[ticker]
            info = TICKERS_BY_SYMBOL[ticker]
            result[ticker] = StockSummary(
                ticker=ticker,
                name=info.name,
                sector=info.sector,
                price=price,
                currency="USD",
                previous_close=None,
                change=None,
                change_percent=None,
                is_stale=True,
                error=None,
            )
        else:
            result[ticker] = failed[ticker]
    return result


def _is_total_failure(summaries: dict[str, StockSummary]) -> bool:
    """True if every summary in a non-empty batch carries an error."""
    return bool(summaries) and all(s.error is not None for s in summaries.values())


def _mark_stale(
    summaries: dict[str, StockSummary],
) -> dict[str, StockSummary]:
    """Return a copy of ``summaries`` with every entry flagged stale."""
    return {
        ticker: summary.model_copy(update={"is_stale": True})
        for ticker, summary in summaries.items()
    }


def _fallback_batch() -> dict[str, StockSummary] | None:
    """Best available batch without touching Alpaca, or ``None`` if there is none.

    Preference order: in-memory stale batch, SQLite minute-row batch, the
    last error-flagged batch.
    """
    stale = _stocks_cache.get_stale(STOCKS_CACHE_KEY)
    if stale is not None:
        return _mark_stale(stale)
    if _backoff.last_failed_batch is not None:
        db_batch = _db_fallback_batch(_backoff.last_failed_batch)
        if db_batch is not None:
            return db_batch
        return _backoff.last_failed_batch
    return None


async def _fetch_stocks_batch() -> dict[str, StockSummary]:
    """Fetch (or return cached/stale/DB-backed) summaries for the whole universe.

    Never raises: a total refresh failure degrades to the last known-good
    batch marked ``is_stale``, then to the SQLite minute-row batch, then
    (cold start, empty DB) to the error-flagged batch itself, so both
    stocks endpoints stay 200.
    """
    if _backoff.is_blocked():
        fallback = _fallback_batch()
        if fallback is not None:
            return fallback

    fetched = False

    async def fetch() -> dict[str, StockSummary]:
        nonlocal fetched
        fetched = True
        summaries, minute_bars = alpaca_client.fetch_summaries(TICKER_SYMBOLS)
        if _is_total_failure(summaries):
            # Do NOT let this be cached: it would overwrite the stale
            # shadow store with 20 empty rows.
            raise _TotalFetchFailure(summaries)
        _persist_minute_bars(
            minute_bars, _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        return summaries

    try:
        summaries = await _stocks_cache.get_or_fetch(STOCKS_CACHE_KEY, fetch)
    except _TotalFetchFailure as exc:
        _backoff.record_failure(exc.summaries)
        stale = _stocks_cache.get_stale(STOCKS_CACHE_KEY)
        if stale is not None:
            return _mark_stale(stale)
        db_batch = _db_fallback_batch(exc.summaries)
        if db_batch is not None:
            return db_batch
        # Cold start with nothing known-good anywhere: hand back the
        # error-flagged batch (200; the frontend greys those rows out).
        return exc.summaries

    if fetched:
        _backoff.record_success()
    return summaries
