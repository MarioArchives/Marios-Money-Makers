"""``/api/stocks`` routes.

Three endpoints, per the plan:

- ``GET /api/stocks`` — leaderboard: all 20 tickers' summaries. Backed by
  a single :class:`app.cache.TTLCacheWithLock` keyed on
  :data:`STOCKS_CACHE_KEY`, whose ``fetch`` invokes
  :func:`app.yfinance_client.fetch_summaries` for the full universe. Never
  500s on partial per-ticker failure — those entries simply carry
  ``is_stale=True``/``error`` (see ``yfinance_client``'s per-ticker fault
  tolerance contract).
- ``GET /api/stocks/{ticker}`` — a single ticker's summary, sourced from
  the same cached batch. 404s for any ticker outside the fixed universe
  in :data:`app.tickers.TICKER_SYMBOLS`.
- ``GET /api/stocks/{ticker}/history`` — intraday points for one ticker,
  backed by a per-ticker-keyed :class:`app.cache.TTLCacheWithLock` whose
  ``fetch`` invokes :func:`app.yfinance_client.fetch_history`. On a fresh
  fetch failure, prefers the cache's last-known-good (stale) value
  (``is_stale=True``) over failing; only when *no* cached value exists at
  all does this endpoint respond 503. 404s for unknown tickers, same as
  above.

Rate-limit hardening
--------------------
``fetch_summaries`` never raises — a fully rate-limited batch comes back
as 20 error-flagged summaries, which look like a *successful* fetch to
``get_or_fetch`` and would otherwise be cached, clobbering the stale
shadow store and blanking the page. So the batch fetch here:

1. Treats a batch in which *every* summary carries an ``error`` as a total
   failure and raises out of ``fetch``, so nothing is cached and the last
   known-good batch survives.
2. Serves that last known-good batch with ``is_stale=True`` instead
   (falling back to the error-flagged batch itself, still 200, on a cold
   start with nothing cached — the frontend renders those rows greyed).
3. Applies bounded exponential backoff between refresh attempts while
   total failures continue, so a rate-limited backend stops hammering
   Yahoo and just serves stale until the window elapses. Reset on the
   first successful batch.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app import yfinance_client
from app.cache import TTLCacheWithLock
from app.config import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    CACHE_TTL_SECONDS,
    HISTORY_CACHE_TTL_SECONDS,
)
from app.schemas import HistoryResponse, StockSummary, StocksResponse
from app.tickers import TICKER_SYMBOLS, TICKERS_BY_SYMBOL

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

# Single cache entry holding the whole 20-ticker batch, so
# GET /api/stocks and GET /api/stocks/{ticker} share one fetch.
STOCKS_CACHE_KEY = "stocks"
_stocks_cache: TTLCacheWithLock[dict[str, StockSummary]] = TTLCacheWithLock(
    ttl=CACHE_TTL_SECONDS
)

# History is cached independently per ticker (a fetch for one ticker's
# chart should not invalidate/refetch another's).
_history_cache: TTLCacheWithLock[HistoryResponse] = TTLCacheWithLock(
    ttl=HISTORY_CACHE_TTL_SECONDS
)

# Monotonic clock, indirected through a module global so tests can
# substitute a fake clock instead of sleeping through backoff windows.
_monotonic = time.monotonic


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
    a total failure serves the last known-good batch marked stale.
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
async def get_stock_history(ticker: str) -> HistoryResponse:
    """Return intraday history points for a single ticker.

    404s for an unknown ticker. On a fresh-fetch failure, serves the last
    cached (stale) value with ``is_stale=True`` if one exists; if none
    exists at all, responds 503 rather than 200-with-empty-points.
    """
    if ticker not in TICKERS_BY_SYMBOL:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    async def fetch() -> HistoryResponse:
        result = yfinance_client.fetch_history(ticker)
        if result.error is not None:
            # yfinance_client never raises for a per-ticker failure; it
            # returns an error result instead. Turn that into an
            # exception here so cache.get_or_fetch does not cache/overwrite
            # the last-known-good stale value with this failed fetch.
            raise RuntimeError(result.error)
        return result

    try:
        return await _history_cache.get_or_fetch(ticker, fetch)
    except Exception:
        stale = _history_cache.get_stale(ticker)
        if stale is not None:
            return stale.model_copy(update={"is_stale": True})
        raise HTTPException(
            status_code=503, detail=f"History unavailable for {ticker}"
        )


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
    """Best available batch without touching Yahoo, or ``None`` if there is none."""
    stale = _stocks_cache.get_stale(STOCKS_CACHE_KEY)
    if stale is not None:
        return _mark_stale(stale)
    if _backoff.last_failed_batch is not None:
        return _backoff.last_failed_batch
    return None


async def _fetch_stocks_batch() -> dict[str, StockSummary]:
    """Fetch (or return cached/stale) summaries for the whole universe.

    Never raises: a total refresh failure degrades to the last known-good
    batch marked ``is_stale``, or (cold start) to the error-flagged batch
    itself, so both stocks endpoints stay 200.
    """
    if _backoff.is_blocked():
        fallback = _fallback_batch()
        if fallback is not None:
            return fallback

    fetched = False

    async def fetch() -> dict[str, StockSummary]:
        nonlocal fetched
        fetched = True
        summaries = yfinance_client.fetch_summaries(TICKER_SYMBOLS)
        if _is_total_failure(summaries):
            # Do NOT let this be cached: it would overwrite the stale
            # shadow store with 20 empty rows.
            raise _TotalFetchFailure(summaries)
        return summaries

    try:
        summaries = await _stocks_cache.get_or_fetch(STOCKS_CACHE_KEY, fetch)
    except _TotalFetchFailure as exc:
        _backoff.record_failure(exc.summaries)
        stale = _stocks_cache.get_stale(STOCKS_CACHE_KEY)
        if stale is not None:
            return _mark_stale(stale)
        # Cold start with nothing known-good: hand back the error-flagged
        # batch as before (200; the frontend greys those rows out).
        return exc.summaries

    if fetched:
        _backoff.record_success()
    return summaries
