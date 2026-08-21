"""Startup + periodic backfill sweep for the SQLite history store.

Without this module, nothing refreshes the ``bars_minute`` / ``bars_hour``
/ ``bars_month`` tables except a user opening a chart, so after the
backend has been down for a while (or has simply had no chart viewers)
most of the 20 tickers' history is frozen or empty. :func:`sweep` walks
every (tier, ticker) pair -- tier-major, so all the cheap minute bars land
before the hourly and daily ones -- and, for each pair whose ``fetch_log``
entry has lapsed past the tier's freshness window, calls
:func:`app.routers.stocks.refresh_history`: the very same lock + fetch +
upsert + ``record_fetch`` + prune path the history endpoint uses, so a
request and the sweep can never double-fetch a pair or disagree about
what "fresh" means. Fresh pairs cost zero Alpaca calls.

Failure policy: the sweep must never crash the process. A rate-limited
fetch pauses ``config.BACKFILL_RATE_LIMIT_PAUSE_SECONDS`` and retries that
pair once more before moving on; any other error is logged (logger
``app.backfill``) and skipped -- the pair stays stale in ``fetch_log`` and
is picked up by the next pass or the next chart request.

:func:`run_forever` runs one sweep immediately and then one every
``config.BACKFILL_INTERVAL_SECONDS``, until cancelled (which
:mod:`app.main`'s lifespan does on shutdown). Config is read via the
``config`` module attributes at call time so tests can monkeypatch it;
``_sleep`` is a module-level seam so tests can skip the waits.
"""

from __future__ import annotations

import asyncio
import logging

from app import config
from app.alpaca_client import AlpacaError
from app.routers import stocks as stocks_router
from app.storage import TIERS
from app.tickers import TICKER_SYMBOLS

logger = logging.getLogger("app.backfill")

# Test seam (monkeypatch to a recording no-op): every wait in this module
# goes through here, never through `asyncio.sleep` directly.
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


async def sweep() -> None:
    """One pass over every (tier, ticker): refresh the stale pairs, skip the fresh."""
    logger.info("backfill: sweep starting")
    for tier in TIERS:
        _timeframe, _window, freshness = stocks_router._SPEC_BY_TIER[tier]
        for ticker in TICKER_SYMBOLS:
            # Cheap pre-check outside the lock; `refresh_history` re-checks
            # inside it, so a concurrent request can't make us double-fetch.
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
