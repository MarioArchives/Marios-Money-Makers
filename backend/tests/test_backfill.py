"""Tests for `app.backfill` (startup + periodic sweep) and its lifespan wiring.

Same conventions as `tests/test_stocks_router.py`: a tmp-path SQLite DB
via a monkeypatched `app.config.DB_PATH`, `app.alpaca_client.fetch_bars`
mocked at the module attribute (the router and the sweep both call it
through the `alpaca_client` module object, and `refresh_history` runs it
in a worker thread, so the patch is visible there too), and a frozen
wall clock via `app.routers.stocks._utcnow`. `app.backfill._sleep` is the
module's sleep seam: it is replaced by a recorder so no test ever waits.
The sweep also refreshes the leaderboard's `summaries` table through the
real `alpaca_client.fetch_summaries`, so an `httpx.MockTransport` answering
the snapshots request (no minute bars, so the bar tables stay untouched)
is installed for every test. No network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.backfill as backfill
import app.routers.market as market_router
import app.routers.stocks as stocks_router
from app import alpaca_client, config, storage
from app.alpaca_client import AlpacaError, Bar, MarketClock
from app.main import app
from app.storage import TIERS
from app.tickers import TICKER_SYMBOLS

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
ALL_PAIRS = [(tier, ticker) for tier in TIERS for ticker in TICKER_SYMBOLS]
assert len(ALL_PAIRS) == 60


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(ts: str, price: float) -> Bar:
    analytics = json.dumps(
        {"o": price, "h": price, "l": price, "c": price, "v": 1000, "vw": price, "n": 10}
    )
    return Bar(ts=ts, price=price, analytics=analytics)


class _FakeWallClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _SleepRecorder:
    """`backfill._sleep` stand-in: records requested durations, never waits.

    Optionally raises `asyncio.CancelledError` on the N-th call so
    `run_forever` tests can stop the loop deterministically.
    """

    def __init__(self, cancel_after: int | None = None) -> None:
        self.calls: list[float] = []
        self.cancel_after = cancel_after

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.cancel_after is not None and len(self.calls) >= self.cancel_after:
            raise asyncio.CancelledError()


def _snapshots_handler(request: httpx.Request) -> httpx.Response:
    """Minimal good snapshots payload: prices, no minuteBar (so nothing is
    persisted into `bars_minute` behind the history assertions' back)."""
    if request.url.path != "/v2/stocks/snapshots":
        return httpx.Response(404)
    symbols = request.url.params["symbols"].split(",")
    body = {
        symbol: {
            "latestTrade": {"p": 100.0 + i},
            "prevDailyBar": {"c": 100.0},
        }
        for i, symbol in enumerate(symbols)
    }
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "stocks.db"))
    monkeypatch.setattr(
        alpaca_client, "_transport", httpx.MockTransport(_snapshots_handler)
    )
    stocks_router._refresh_locks = {}
    stocks_router._backoff = stocks_router._BackoffState()
    storage.init_db()
    yield


@pytest.fixture()
def fake_utcnow(monkeypatch):
    clock = _FakeWallClock()
    monkeypatch.setattr(stocks_router, "_utcnow", clock)
    return clock


@pytest.fixture()
def sleeps(monkeypatch):
    recorder = _SleepRecorder()
    monkeypatch.setattr(backfill, "_sleep", recorder)
    return recorder


def _fetch_log() -> dict[tuple[str, str], str | None]:
    return {(tier, t): storage.last_fetch_at(tier, t) for tier, t in ALL_PAIRS}


def _calls(mock_fetch) -> list[tuple[str, str]]:
    """(symbol, timeframe) per `fetch_bars` call, in call order."""
    out = []
    for call in mock_fetch.call_args_list:
        symbol, timeframe = call.args[0], call.args[1]
        out.append((symbol, timeframe))
    return out


class TestSweep:
    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_empty_db_fetches_every_pair_once_tier_major_and_stamps_fetch_log(
        self, mock_fetch, fake_utcnow, sleeps
    ):
        mock_fetch.return_value = [_bar(_iso(NOW - timedelta(minutes=1)), 100.0)]

        await backfill.sweep()

        assert mock_fetch.call_count == 60
        calls = _calls(mock_fetch)
        # Tier-major: all 20 minute fetches, then 20 hourly, then 20 daily,
        # each block in TICKER_SYMBOLS order.
        assert calls[:20] == [(t, "1Min") for t in TICKER_SYMBOLS]
        assert calls[20:40] == [(t, "1Hour") for t in TICKER_SYMBOLS]
        assert calls[40:] == [(t, "1Day") for t in TICKER_SYMBOLS]
        assert _fetch_log() == {pair: _iso(NOW) for pair in ALL_PAIRS}
        # Bars landed in every tier table.
        for tier in TIERS:
            assert storage.get_bars(tier, "AAPL", since="") == [
                (_iso(NOW - timedelta(minutes=1)), 100.0)
            ]
        assert sleeps.calls == []

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_fresh_pairs_are_skipped_with_zero_alpaca_calls(
        self, mock_fetch, fake_utcnow, sleeps
    ):
        for tier, ticker in ALL_PAIRS:
            storage.record_fetch(tier, ticker, _iso(NOW))

        await backfill.sweep()

        assert mock_fetch.call_count == 0
        assert _fetch_log() == {pair: _iso(NOW) for pair in ALL_PAIRS}

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_only_lapsed_tiers_are_refetched(
        self, mock_fetch, fake_utcnow, sleeps
    ):
        # Everything fresh as of NOW; advance past the minute window only.
        for tier, ticker in ALL_PAIRS:
            storage.record_fetch(tier, ticker, _iso(NOW))
        mock_fetch.return_value = []
        fake_utcnow.advance(config.FRESHNESS_MINUTE_SECONDS + 1)

        await backfill.sweep()

        assert _calls(mock_fetch) == [(t, "1Min") for t in TICKER_SYMBOLS]
        log = _fetch_log()
        for ticker in TICKER_SYMBOLS:
            assert log[("minute", ticker)] == _iso(fake_utcnow.now)
            assert log[("hour", ticker)] == _iso(NOW)
            assert log[("days", ticker)] == _iso(NOW)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_rate_limit_pauses_then_retries_that_pair_and_continues(
        self, mock_fetch, fake_utcnow, sleeps, monkeypatch
    ):
        monkeypatch.setattr(config, "BACKFILL_RATE_LIMIT_PAUSE_SECONDS", 7.5)
        calls: list[tuple[str, str]] = []

        def fetch(symbol, timeframe, start):
            calls.append((symbol, timeframe))
            # First attempt on MSFT/minute is throttled; the retry succeeds.
            if (symbol, timeframe) == ("MSFT", "1Min") and calls.count(
                (symbol, timeframe)
            ) == 1:
                raise AlpacaError("alpaca rate limited", is_rate_limit=True)
            return [_bar(_iso(NOW - timedelta(minutes=1)), 1.0)]

        mock_fetch.side_effect = fetch

        await backfill.sweep()

        assert sleeps.calls == [7.5]
        assert mock_fetch.call_count == 61
        assert calls.count(("MSFT", "1Min")) == 2
        # Every pair, MSFT/minute included, ended up stamped.
        assert _fetch_log() == {pair: _iso(NOW) for pair in ALL_PAIRS}

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_rate_limit_twice_moves_on_after_one_retry(
        self, mock_fetch, fake_utcnow, sleeps
    ):
        def fetch(symbol, timeframe, start):
            if (symbol, timeframe) == ("MSFT", "1Min"):
                raise AlpacaError("alpaca rate limited", is_rate_limit=True)
            return []

        mock_fetch.side_effect = fetch

        await backfill.sweep()

        # One pause, one retry, then the remaining 59 pairs still ran.
        assert sleeps.calls == [config.BACKFILL_RATE_LIMIT_PAUSE_SECONDS]
        assert mock_fetch.call_count == 61
        log = _fetch_log()
        assert log[("minute", "MSFT")] is None
        assert all(v == _iso(NOW) for pair, v in log.items() if pair != ("minute", "MSFT"))

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_non_rate_limit_error_on_one_pair_does_not_stop_the_others(
        self, mock_fetch, fake_utcnow, sleeps, caplog
    ):
        def fetch(symbol, timeframe, start):
            if (symbol, timeframe) == ("AAPL", "1Hour"):
                raise AlpacaError("alpaca error: boom")
            if (symbol, timeframe) == ("MSFT", "1Day"):
                raise RuntimeError("unexpected")
            return []

        mock_fetch.side_effect = fetch

        with caplog.at_level("WARNING", logger="app.backfill"):
            await backfill.sweep()

        # No retry for non-rate-limit failures, no pause, everything else ran.
        assert sleeps.calls == []
        assert mock_fetch.call_count == 60
        log = _fetch_log()
        assert log[("hour", "AAPL")] is None
        assert log[("days", "MSFT")] is None
        failed = {("hour", "AAPL"), ("days", "MSFT")}
        assert all(v == _iso(NOW) for pair, v in log.items() if pair not in failed)
        assert any("hour/AAPL" in r.getMessage() for r in caplog.records)
        assert any("days/MSFT" in r.getMessage() for r in caplog.records)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_sweep_shares_lock_with_endpoint_refresh(
        self, mock_fetch, fake_utcnow, sleeps
    ):
        # `refresh_history` is the single write path: a pair the endpoint's
        # refresh has just done is fresh, so the sweep skips it.
        mock_fetch.return_value = []
        assert await stocks_router.refresh_history("AAPL", "minute") is True
        assert await stocks_router.refresh_history("AAPL", "minute") is False
        mock_fetch.reset_mock()

        await backfill.sweep()

        assert ("AAPL", "1Min") not in _calls(mock_fetch)
        assert mock_fetch.call_count == 59


class TestRunForever:
    async def test_sweeps_immediately_then_sleeps_interval_between_sweeps(
        self, monkeypatch
    ):
        monkeypatch.setattr(config, "BACKFILL_INTERVAL_SECONDS", 123.0)
        recorder = _SleepRecorder(cancel_after=3)
        monkeypatch.setattr(backfill, "_sleep", recorder)
        sweeps = 0

        async def fake_sweep() -> None:
            nonlocal sweeps
            sweeps += 1

        monkeypatch.setattr(backfill, "sweep", fake_sweep)

        with pytest.raises(asyncio.CancelledError):
            await backfill.run_forever()

        assert recorder.calls == [123.0, 123.0, 123.0]
        assert sweeps == 3

    async def test_task_cancel_stops_cleanly(self, monkeypatch):
        started = asyncio.Event()

        async def fake_sweep() -> None:
            started.set()

        async def real_sleep(seconds: float) -> None:
            await asyncio.sleep(seconds)

        monkeypatch.setattr(backfill, "sweep", fake_sweep)
        monkeypatch.setattr(config, "BACKFILL_INTERVAL_SECONDS", 3600.0)
        monkeypatch.setattr(backfill, "_sleep", real_sleep)

        task = asyncio.create_task(backfill.run_forever())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()


class TestLifespanWiring:
    def test_enabled_starts_sweep_on_startup_and_cancels_on_shutdown(
        self, monkeypatch
    ):
        monkeypatch.setattr(config, "BACKFILL_ENABLED", True)
        events: list[str] = []

        async def fake_run_forever() -> None:
            events.append("started")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("cancelled")
                raise

        monkeypatch.setattr(backfill, "run_forever", fake_run_forever)

        with TestClient(app) as client:
            assert client.get("/api/stocks/AAPL/stored").status_code == 200
            assert events == ["started"]
        assert events == ["started", "cancelled"]

    def test_disabled_never_starts_sweep(self, monkeypatch):
        # conftest already forces BACKFILL_ENABLED off; make it explicit.
        monkeypatch.setattr(config, "BACKFILL_ENABLED", False)
        events: list[str] = []

        async def fake_run_forever() -> None:
            events.append("started")

        monkeypatch.setattr(backfill, "run_forever", fake_run_forever)

        with TestClient(app):
            pass
        assert events == []


class TestLifespanAdjustmentMigration:
    def test_lifespan_records_adjustment_and_clears_stale_bar_stamps(
        self, monkeypatch
    ):
        monkeypatch.setattr(config, "ALPACA_BARS_ADJUSTMENT", "split")
        # Legacy DB: bars + stamps but no meta key -> fetched as `raw`.
        storage.upsert_bars("days", "NFLX", [_bar(_iso(NOW), 1112.1)], _iso(NOW))
        storage.record_fetch("days", "NFLX", _iso(NOW))
        storage.record_fetch(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY, _iso(NOW))

        with TestClient(app):
            pass

        assert storage.get_meta(storage.BARS_ADJUSTMENT_META_KEY) == "split"
        assert storage.last_fetch_at("days", "NFLX") is None
        assert storage.last_fetch_at(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY) == _iso(NOW)
        assert len(storage.get_stored_rows("days", "NFLX")) == 1


class TestConfigDefaults:
    def test_defaults(self):
        assert isinstance(config.BACKFILL_INTERVAL_SECONDS, float)
        assert config.BACKFILL_INTERVAL_SECONDS == 600.0
        assert config.BACKFILL_RATE_LIMIT_PAUSE_SECONDS == 60.0
        assert config.ALPACA_BARS_ADJUSTMENT == "split"


class TestSweepReachesBackToOldestDailyBar:
    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_days_pair_start_is_the_oldest_stored_row(
        self, mock_fetch, fake_utcnow, sleeps
    ):
        # The sweep goes through `refresh_history`, so a daily bar that has
        # aged out of the 365d window still widens AAPL's 1Day request;
        # other tickers' daily pairs keep the plain window.
        ancient = _iso(NOW - timedelta(days=3 * 365))
        storage.upsert_bars("days", "AAPL", [_bar(ancient, 42.0)], _iso(NOW))
        mock_fetch.return_value = []

        await backfill.sweep()

        starts = {
            (call.args[0], call.args[1]): call.args[2]
            for call in mock_fetch.call_args_list
        }
        assert starts[("AAPL", "1Day")] == NOW - timedelta(days=3 * 365)
        assert starts[("MSFT", "1Day")] == NOW - timedelta(days=365)
        assert starts[("AAPL", "1Min")] == NOW - timedelta(hours=24)
        assert starts[("AAPL", "1Hour")] == NOW - timedelta(days=30)


class TestSweepRefreshesMarketClock:
    """Every pass also refreshes the Alpaca market clock into `meta` (via the
    market router's `refresh_clock`), so the stored market status is kept
    current without a browser. A clock failure is logged and never stops
    the pair refreshes."""

    CLOCK = MarketClock(
        timestamp=_iso(NOW),
        is_open=False,
        next_open=_iso(NOW + timedelta(hours=1, minutes=30)),
        next_close=_iso(NOW + timedelta(hours=8)),
    )

    @pytest.fixture(autouse=True)
    def _market_clock_seams(self, monkeypatch, fake_utcnow):
        # The market router has its own `_utcnow` seam; drive it with the
        # same frozen clock as the stocks router.
        monkeypatch.setattr(market_router, "_utcnow", fake_utcnow)
        market_router._refresh_locks = {}
        yield

    @patch("app.routers.market.alpaca_client.fetch_clock")
    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_sweep_stores_the_clock_in_meta_once_per_pass(
        self, mock_fetch, mock_clock, sleeps
    ):
        mock_fetch.return_value = []
        mock_clock.return_value = self.CLOCK
        assert storage.get_market_clock() is None

        await backfill.sweep()

        assert mock_clock.call_count == 1
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.is_open is False
        assert stored.next_open == self.CLOCK.next_open
        assert stored.fetched_at == _iso(NOW)
        # The pair refreshes still ran in full.
        assert mock_fetch.call_count == 60

    @patch("app.routers.market.alpaca_client.fetch_clock")
    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_fresh_clock_is_not_refetched(self, mock_fetch, mock_clock, sleeps):
        mock_fetch.return_value = []
        storage.set_market_clock(self.CLOCK, fetched_at=_iso(NOW))

        await backfill.sweep()

        assert mock_clock.call_count == 0

    @patch("app.routers.market.alpaca_client.fetch_clock")
    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_clock_failure_is_logged_and_does_not_stop_the_pass(
        self, mock_fetch, mock_clock, sleeps, caplog
    ):
        import logging

        mock_fetch.return_value = []
        mock_clock.side_effect = AlpacaError("alpaca error: clock down")

        with caplog.at_level(logging.WARNING):
            await backfill.sweep()

        assert storage.get_market_clock() is None
        assert mock_fetch.call_count == 60
        assert any("clock" in record.getMessage() for record in caplog.records)
