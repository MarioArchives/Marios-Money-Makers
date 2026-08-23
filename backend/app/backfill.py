"""Startup + periodic backfill sweep for the SQLite history store.

Reuses the endpoints' own lock/fetch/upsert path, so a request and the sweep
never double-fetch a pair or disagree about "fresh". Must never crash.
"""

from __future__ import annotations

import asyncio
import logging

from app import config
from app.alpaca_client import AlpacaError
from app.routers import market as market_router
from app.routers import stocks as stocks_router
from app.storage import TIERS
from app.tickers import TICKER_SYMBOLS

logger = logging.getLogger("app.backfill")

# Test seam: every wait goes through here, never `asyncio.sleep` directly.
_sleep = asyncio.sleep


async def _refresh_pair(ticker: str, tier: str) -> None:
    """Refresh one pair, retrying once after a rate-limit pause; never raises."""
    for attempt in (1, 2):
        try:
            if await stocks_router.refresh_history(ticker, tier):
                logger.debug("backfill: refreshed %s/%s", tier, ticker)
            return
        except AlpacaError as exc:
            if exc.is_rate_limit and attempt == 1:
                logger.warning(
                    "backfill: rate limited on %s/%s; pausing %ss before retry",
                    tier,
                    ticker,
                    config.BACKFILL_RATE_LIMIT_PAUSE_SECONDS,
                )
                await _sleep(config.BACKFILL_RATE_LIMIT_PAUSE_SECONDS)
                continue
            logger.warning("backfill: %s/%s failed: %s", tier, ticker, exc)
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the sweep must never crash
            logger.exception("backfill: unexpected error on %s/%s", tier, ticker)
            return


async def _refresh_summaries() -> None:
    """Refresh the leaderboard's ``summaries`` table if stale; never raises."""
    try:
        await stocks_router.refresh_summaries()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the sweep must never crash
        logger.exception("backfill: unexpected error refreshing summaries")


async def _refresh_clock() -> None:
    """Refresh the cached market clock (`meta`) if stale; never raises."""
    try:
        await market_router.refresh_clock()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the sweep must never crash
        logger.exception("backfill: unexpected error refreshing market clock")


async def sweep() -> None:
    """One pass: refresh the leaderboard table, the market clock, then
    every stale (tier, ticker) pair."""
    logger.info("backfill: sweep starting")
    await _refresh_summaries()
    await _refresh_clock()
    for tier in TIERS:
        _timeframe, _window, freshness = stocks_router._SPEC_BY_TIER[tier]
        for ticker in TICKER_SYMBOLS:
            # Pre-check outside the lock; `refresh_history` re-checks inside it.
            if stocks_router._is_history_fresh(tier, ticker, freshness):
                continue
            await _refresh_pair(ticker, tier)
    logger.info("backfill: sweep finished")


async def run_forever() -> None:
    """Sweep now, then every ``config.BACKFILL_INTERVAL_SECONDS`` until cancelled."""
    try:
        while True:
            await sweep()
            await _sleep(config.BACKFILL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("backfill: stopped")
        raise
