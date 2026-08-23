"""``/api/market`` routes: the Alpaca market clock.

One endpoint, ``GET /clock``. The ``meta`` table (key
:data:`app.storage.MARKET_CLOCK_META_KEY`) IS the cache -- there is no
in-memory copy, exactly like the leaderboard's ``summaries`` table and
history's bar tables.

Freshness rule: the stored clock is served with zero Alpaca calls until
one of the boundaries it carries arrives -- ``now`` must be strictly
before BOTH ``next_open`` and ``next_close``. The state a clock describes
cannot change before then, so nothing else expires it: one fetched on
Friday afternoon is still the right answer on Sunday night ("closed,
opens Monday 09:30"), and the moment that open arrives it is wrong data
and must be refetched.

There is deliberately no time-based TTL on top of that. It would refetch
clocks that are still correct -- a browser polling every 20s would mean
~1000 Alpaca calls a day -- to narrow the window on the one thing a
boundary check cannot see: a session the exchange re-schedules *after* we
cached it (an unscheduled closure or early close). That is rare enough to
accept; such a change is picked up at the next boundary crossing. An
unparseable stored boundary counts as stale, so a corrupt row is
refetched rather than trusted.

Single flight, same shape as the stocks router: an in-process
``asyncio.Lock`` (double-checked freshness after acquiring) plus a
cross-process lease row in ``fetch_claims`` under the pseudo-pair
``(storage.CLOCK_TIER, storage.CLOCK_KEY)`` -- a lease key only, there is
no ``bars_clock`` table. A process that finds the lease held by someone
else simply serves the stored clock (marked stale if it's outside the
freshness window) rather than fetching too.

Failure policy: an Alpaca failure serves the stored clock with
``is_stale=True`` and ``error`` set (the ``meta`` row is left untouched),
UNLESS nothing has ever been stored, in which case the request 503s. No
backoff -- unlike the leaderboard, a failed clock fetch does not block the
very next request from trying Alpaca again.

:func:`refresh_clock` is the same refresh, exposed for
:mod:`app.backfill`'s periodic sweep: it swallows any
:class:`app.alpaca_client.AlpacaError` (logged at WARNING) so a clock
outage never stops the sweep's bar-tier pass.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app import alpaca_client, config, storage
from app.alpaca_client import AlpacaError
from app.routers.stocks import _iso, _parse_iso
from app.schemas import MarketClockResponse

logger = logging.getLogger("app.routers.market")

router = APIRouter(prefix="/api/market", tags=["market"])

# In-process single-flight locks, keyed like `stocks._refresh_locks`
# (there is only ever one key here, `storage.CLOCK_TIER`). Tests reset
# this to `{}` between cases.
_refresh_locks: dict[str, asyncio.Lock] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_fresh(stored: storage.StoredClock, now: datetime) -> bool:
    """True while the stored clock still describes the present: ``now`` is
    before both boundaries it carries. ``fetched_at`` plays no part (see
    the module docstring on why there is no TTL); an unparseable boundary
    counts as stale."""
    next_open_dt = _parse_iso(stored.next_open)
    if next_open_dt is None or now >= next_open_dt:
        return False
    next_close_dt = _parse_iso(stored.next_close)
    if next_close_dt is None or now >= next_close_dt:
        return False
    return True


async def _get_clock() -> tuple[storage.StoredClock, bool, str | None]:
    """Return ``(stored_clock, is_stale, error)``, refreshing via Alpaca
    when the stored clock is missing or stale (see the module docstring).

    Raises :class:`AlpacaError` iff there is nothing to serve: either the
    fetch failed and nothing was ever stored, or the cross-process lease
    is held elsewhere and nothing was ever stored. Callers decide how to
    surface that (503 for the endpoint, logged-and-swallowed for the
    sweep's :func:`refresh_clock`).
    """
    storage.init_db()
    now = _utcnow()
    stored = storage.get_market_clock()
    if stored is not None and _is_fresh(stored, now):
        return stored, False, None

    lock = _refresh_locks.setdefault(storage.CLOCK_TIER, asyncio.Lock())
    async with lock:
        # Double-checked: the leader we queued behind may have refreshed
        # (serve fresh) or failed (serve stale, no backoff -- try again on
        # the next request) meanwhile.
        now = _utcnow()
        stored = storage.get_market_clock()
        if stored is not None and _is_fresh(stored, now):
            return stored, False, None

        claim_id = storage.try_claim(
            storage.CLOCK_TIER, storage.CLOCK_KEY, _iso(now), config.FETCH_LEASE_SECONDS
        )
        if claim_id is None:
            # Another process holds the lease and is fetching right now;
            # serve what is stored (it may even have landed fresh since
            # our check above).
            stored = storage.get_market_clock()
            if stored is None:
                raise AlpacaError(
                    f"{alpaca_client.ERROR_PREFIX}: market clock refresh is "
                    "already in progress on another process and nothing "
                    "is cached yet"
                )
            return stored, not _is_fresh(stored, _utcnow()), None

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
        finally:
            # Fenced by our claim_id: a release after another process took
            # an expired lease over is a no-op (its claim stays in place).
            storage.release_claim(storage.CLOCK_TIER, storage.CLOCK_KEY, claim_id)


@router.get("/clock", response_model=MarketClockResponse)
async def get_market_clock() -> MarketClockResponse:
    """Return Alpaca's market clock, cached in SQLite.

    See the module docstring for the freshness/single-flight/failure
    rules. 503s only when Alpaca is down (or its lease is held elsewhere)
    AND nothing has ever been cached.
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
    """Refresh the cached market clock if it is stale; used by
    :mod:`app.backfill`'s sweep.

    Same fresh-skipping, single-flight refresh as the endpoint, but never
    raises for Alpaca reasons: a failure is logged at WARNING and
    swallowed so a clock outage never stops the sweep's bar-tier pass.
    """
    try:
        await _get_clock()
    except AlpacaError as exc:
        logger.warning("market clock refresh failed: %s", exc)
