"""``/api/market`` routes: the Alpaca market clock, cached in the ``meta``
table with no TTL — see README.md for the freshness/single-flight/failure
contract. ``GET /clock`` 503s only when Alpaca is down and nothing is cached.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app import alpaca_client, storage
from app.alpaca_client import AlpacaError
from app.routers.stocks import _iso, _parse_iso
from app.schemas import MarketClockResponse

logger = logging.getLogger("app.routers.market")

router = APIRouter(prefix="/api/market", tags=["market"])

# In-process single-flight lock; tests reset this to `{}` between cases.
_CLOCK_LOCK_KEY = "clock"
_refresh_locks: dict[str, asyncio.Lock] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_fresh(stored: storage.StoredClock, now: datetime) -> bool:
    """True while ``now`` is before both ``next_open`` and ``next_close``;
    deliberately no TTL on top of this (``fetched_at`` plays no part) — an
    unparseable boundary counts as stale."""
    next_open_dt = _parse_iso(stored.next_open)
    if next_open_dt is None or now >= next_open_dt:
        return False
    next_close_dt = _parse_iso(stored.next_close)
    if next_close_dt is None or now >= next_close_dt:
        return False
    return True


async def _get_clock() -> tuple[storage.StoredClock, bool, str | None]:
    """Return ``(stored_clock, is_stale, error)``, refreshing via Alpaca when
    missing/stale. Raises :class:`AlpacaError` iff the fetch failed and
    nothing was ever stored."""
    storage.init_db()
    now = _utcnow()
    stored = storage.get_market_clock()
    if stored is not None and _is_fresh(stored, now):
        return stored, False, None

    lock = _refresh_locks.setdefault(_CLOCK_LOCK_KEY, asyncio.Lock())
    async with lock:
        # Double-checked: the leader we queued behind may have refreshed already.
        now = _utcnow()
        stored = storage.get_market_clock()
        if stored is not None and _is_fresh(stored, now):
            return stored, False, None

        try:
            clock = await asyncio.to_thread(alpaca_client.fetch_clock)
            fetched_at = _iso(_utcnow())
            storage.set_market_clock(clock, fetched_at)
            return (
                storage.StoredClock(
                    timestamp=clock.timestamp,
                    is_open=clock.is_open,
                    next_open=clock.next_open,
                    next_close=clock.next_close,
                    fetched_at=fetched_at,
                ),
                False,
                None,
            )
        except AlpacaError as exc:
            stored = storage.get_market_clock()
            if stored is None:
                raise
            return stored, True, str(exc)


@router.get("/clock", response_model=MarketClockResponse)
async def get_market_clock() -> MarketClockResponse:
    """Return Alpaca's market clock, cached in SQLite.

    Raises 503 only when Alpaca is down and nothing has ever been cached.
    """
    try:
        stored, is_stale, error = await _get_clock()
    except AlpacaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return MarketClockResponse(
        timestamp=stored.timestamp,
        is_open=stored.is_open,
        next_open=stored.next_open,
        next_close=stored.next_close,
        fetched_at=stored.fetched_at,
        is_stale=is_stale,
        error=error,
    )


async def refresh_clock() -> None:
    """Refresh the cached market clock if stale, for :mod:`app.backfill`'s
    sweep; never raises for Alpaca reasons (logged at WARNING and swallowed)."""
    try:
        await _get_clock()
    except AlpacaError as exc:
        logger.warning("market clock refresh failed: %s", exc)
