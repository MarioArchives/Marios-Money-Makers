"""``/api/stocks`` routes.

Three endpoints:

- ``GET /api/stocks`` — leaderboard: all 20 tickers' summaries. DB-first,
  exactly like history: the SQLite ``summaries`` table (one current-state
  row per ticker, 20 rows, ever) IS the cache -- there is no in-memory
  copy. If the last successful snapshots fetch, stamped in ``fetch_log``
  under ``(tier='summaries', ticker='*')``, is within
  ``CACHE_TTL_SECONDS`` the table is served with zero Alpaca calls;
  otherwise ONE worker refreshes it via
  :func:`app.alpaca_client.fetch_summaries` (one snapshots request for the
  full universe, run in a worker thread) and writes all 20 rows + the
  stamp atomically. Single flight is guaranteed by the ``"summaries"``
  entry of ``_refresh_locks``, an in-process ``asyncio.Lock`` with
  double-checked freshness after acquiring -- this app only ever runs as
  one process, so that lock alone is the whole guarantee; there used to
  also be a cross-process lease row in a ``fetch_claims`` table, removed
  because nothing here ever runs as more than one process. Net effect:
  one Alpaca snapshots call per ``CACHE_TTL_SECONDS`` window per database,
  however many requests arrive concurrently, and the last good leaderboard
  survives a restart. ``change``/``change_percent`` are derived on read.
  Never 500s on partial per-ticker failure — those entries simply carry
  ``is_stale=True``/``error`` (and are stored as error rows). Each
  successful batch also best-effort persists every ticker's latest minute
  bar into ``bars_minute`` (a DB hiccup must never fail the endpoint).
  :mod:`app.backfill`'s sweep calls the same refresh at the start of every
  pass, so a restarted backend repopulates the board before any browser
  polls.
- ``GET /api/stocks/{ticker}`` — a single ticker's summary, sourced from
  the same batch. 404s for any ticker outside the fixed universe in
  :data:`app.tickers.TICKER_SYMBOLS`.
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
  window on the read). The Alpaca fetch is bounded to
  ``DAY_BACKFILL_DAYS`` on the first fetch, but on every later refresh it
  also reaches back to the oldest stored daily bar (:func:`_fetch_start`),
  so a row that has aged out of that window is still re-checked -- no
  straggler is left behind un-refreshed, and a split that re-adjusts old
  history is picked up. One paginated ``1Day`` request comfortably covers
  even a multi-year reach-back (252 bars/year, 1000 bars per Alpaca page).
  The series therefore grows by one bar per trading day for as long as the
  store lives.
- ``GET /api/stocks/{ticker}/stored?tier=minute|hour|days`` — raw
  inspection of the SQLite store for one ticker: every row of the tier's
  table (all columns, ``analytics`` JSON parsed, no time window, oldest
  first), the row count per tier and the tier's last successful Alpaca
  fetch time. Read-only: never calls Alpaca, never writes. 404 for
  unknown tickers, 422 for unknown tiers.

Rate-limit hardening (summaries)
--------------------------------
``fetch_summaries`` never raises — a fully rate-limited batch comes back
as 20 error-flagged summaries, which would otherwise be written over the
last good rows and blank the page. So :func:`_get_summaries_batch`:

1. Treats a batch in which *every* summary carries an ``error`` as a total
   failure: nothing is written (the ``summaries`` table and its stamp keep
   the last known-good batch), and the failure is recorded ONCE -- by the
   leader only. Waiters queued on the lock re-check backoff after
   acquiring it and must not retry.
2. Serves, in order of preference: the ``summaries`` table flagged
   ``is_stale=True`` (price AND previous_close/change preserved); the
   last error-flagged batch (``_backoff.last_failed_batch``); an
   ``"alpaca error: no data yet"`` batch built locally. Always 200 — the
   frontend renders error rows greyed.
3. Applies bounded exponential backoff (in-process, ``_BackoffState``)
   between refresh attempts while total failures continue: while blocked
   every request serves the table stale, zero Alpaca calls. Reset on the
   first successful batch.
4. Caps every Alpaca request at ``ALPACA_TIMEOUT_SECONDS`` so a hung
   request can never stall the single in-process lock indefinitely.

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

# Neither the leaderboard nor history has an in-memory cache: SQLite is the
# cache (`summaries` + bars tables + `fetch_log`). The only in-memory
# refresh state is `_refresh_locks`: one asyncio.Lock per key --
# `SUMMARIES_LOCK_KEY` for the leaderboard batch, "{ticker}:{tier}" per
# history pair -- collapsing concurrent cache-miss refreshes in this
# process into one fetch. That lock is the whole single-flight guarantee:
# this app only ever runs as a single process (no cross-process lease).
SUMMARIES_LOCK_KEY = "summaries"
_refresh_locks: dict[str, asyncio.Lock] = {}

# Error text for a leaderboard row we have never managed to fetch.
NO_DATA_ERROR = f"{alpaca_client.ERROR_PREFIX}: no data yet"

# range query param -> (tier, Alpaca timeframe, fetch window, freshness
# seconds, response `interval` label). The fetch window normally bounds the
# Alpaca request (`start = now - window`), but for the never-pruned daily
# tier `_fetch_start` widens that lower bound back to the oldest stored row
# so no bar is left un-refreshed once it ages past the window (see
# `_UNBOUNDED_READ_TIERS`). The *read* window is the same for the pruned
# tiers but unbounded for the never-pruned daily tier (see `_read_since`),
# which is what makes `all` grow over time.
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
        # TIMEFRAME_DAYS == "1Day": the "days" tier's individual bars are
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
# their retention window, which `prune` keeps in step anyway. Also drives
# `_fetch_start`: the same tiers get their Alpaca fetch widened back to the
# oldest stored row, so nothing served by the unbounded read is ever left
# un-refreshed.
_UNBOUNDED_READ_TIERS = frozenset({"days"})

StoredTier = Literal["minute", "hour", "days"]

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


def _fetch_start(tier: str, ticker: str, window: timedelta, now: datetime) -> datetime:
    """Lower bound for the Alpaca fetch ``start`` for ``(ticker, tier)``.

    For the never-pruned daily tier (see ``_UNBOUNDED_READ_TIERS``), widens
    the plain ``now - window`` start back to ``storage.oldest_ts`` when a
    row is stored older than the window -- so a bar that has aged out of
    the backfill window is still re-checked on every refresh (no
    straggler), and a corporate action that re-adjusts old history is
    picked up. Falls back to ``now - window`` when nothing is stored yet or
    the stored timestamp doesn't parse. Other tiers are unaffected.
    """
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
    a total failure serves the stored table marked stale.
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
    ``_refresh_locks`` (collapsing concurrent refreshes in this process
    into one fetch), re-checks ``fetch_log`` freshness inside the lock
    (returns ``False`` with zero Alpaca calls if another task already
    refreshed), then runs :func:`app.alpaca_client.fetch_bars` in a worker
    thread -- it is a blocking httpx call, and must not stall the event
    loop for the other requests (or the sweep) -- and upserts the bars,
    stamps ``fetch_log`` and prunes. Returns ``True`` when a fetch
    happened.

    The fetch's ``start`` comes from :func:`_fetch_start`, not a plain
    ``now - window``: for the never-pruned daily tier it reaches back to
    the oldest stored daily bar so no row is left un-refreshed once it ages
    past the ordinary backfill window.

    :class:`app.alpaca_client.AlpacaError` propagates: the caller decides
    whether the DB can cover for the outage (the endpoint serves stale
    rows; the sweep logs and moves on). Nothing is written on failure, so
    ``fetch_log`` keeps marking the pair stale and the next caller retries.
    """
    if tier not in _SPEC_BY_TIER:
        raise ValueError(f"unknown tier: {tier!r}")
    timeframe, window, freshness = _SPEC_BY_TIER[tier]
    lock = _refresh_locks.setdefault(f"{ticker}:{tier}", asyncio.Lock())

    async with lock:
        # Double-checked freshness: another task racing us on the same
        # (ticker, tier) may have already refreshed while we waited for
        # the lock.
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


def _is_total_failure(summaries: dict[str, StockSummary]) -> bool:
    """True if every summary in a non-empty batch carries an error."""
    return bool(summaries) and all(s.error is not None for s in summaries.values())


def _is_summaries_fresh() -> bool:
    """True if the last successful snapshots fetch is within ``CACHE_TTL_SECONDS``.

    Reads the ``fetch_log`` stamp ``(summaries, *)`` that
    ``storage.upsert_summaries`` writes atomically with the rows.
    """
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
    """Build the leaderboard batch from the ``summaries`` table.

    ``change``/``change_percent`` are derived here (``price -
    previous_close``, ``/ previous_close * 100``), never stored. A row
    carrying an ``error`` stays an error entry (``is_stale=True``); every
    other row is flagged per ``stale``. A ticker missing from the table
    (it should never be: the table always holds the whole universe) gets
    a ``NO_DATA_ERROR`` entry. Returns ``None`` when the table is empty.
    """
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
    """Best batch available without touching Alpaca (never ``None``).

    Preference: the ``summaries`` table flagged stale; the last all-error
    batch (cold start that has never succeeded, while backing off); an
    all-``NO_DATA_ERROR`` batch built locally.
    """
    table = _batch_from_table(stale=True)
    if table is not None:
        return table
    if _backoff.last_failed_batch is not None:
        return _backoff.last_failed_batch
    return {ticker: _no_data_summary(ticker) for ticker in TICKER_SYMBOLS}


async def _get_summaries_batch() -> dict[str, StockSummary]:
    """Return the leaderboard batch, refreshing the ``summaries`` table if stale.

    DB-first with single flight (see the module docstring):

    1. ``fetch_log`` stamp within ``CACHE_TTL_SECONDS`` -> serve the table
       (``is_stale=False``), zero Alpaca calls.
    2. Backoff active -> serve the table stale, zero Alpaca calls.
    3. Take the in-process ``SUMMARIES_LOCK_KEY`` lock, re-check 1 and 2
       (a leader that just refreshed or just failed settles it for every
       waiter).
    4. Fetch in a worker thread. Total failure -> record ONE backoff
       failure, write nothing, serve the table stale. Success -> write all
       rows + stamp atomically, persist minute bars (best-effort), reset
       backoff, return the fresh batch.

    Never raises for Alpaca reasons (``fetch_summaries`` never does);
    anything unexpected propagates after releasing the lock.
    """
    if _is_summaries_fresh():
        fresh = _batch_from_table(stale=False)
        if fresh is not None:
            return fresh
    if _backoff.is_blocked():
        return _stale_fallback()

    lock = _refresh_locks.setdefault(SUMMARIES_LOCK_KEY, asyncio.Lock())
    async with lock:
        # Double-checked: the leader we queued behind may have refreshed
        # (serve fresh) or failed (serve stale -- do NOT retry) meanwhile.
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
    """Refresh the ``summaries`` table if it is stale.

    Thin wrapper around :func:`_get_summaries_batch` for callers that only
    want the side effect (the :mod:`app.backfill` sweep): fresh-skipping,
    backoff-aware, single flight, never raises for Alpaca reasons.
    """
    await _get_summaries_batch()
