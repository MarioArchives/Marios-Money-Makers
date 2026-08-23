"""HTTP-layer tests for `/api/stocks`. `alpaca_client` is mocked; `storage`
(SQLite) is real and under test via a per-test tmp-path DB. `_prime_stale`
seeds the `summaries` table to simulate "a batch already succeeded once"."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.routers.stocks as stocks_router
from app import config, storage
from app.alpaca_client import Bar, AlpacaError
from app.config import (
    ALLOWED_ORIGINS,
    CACHE_TTL_SECONDS,
    FRESHNESS_MINUTE_SECONDS,
)
from app.main import app
from app.schemas import StockSummary
from app.tickers import TICKER_SYMBOLS, TICKERS_BY_SYMBOL

ALLOWED_ORIGIN = ALLOWED_ORIGINS[0]
assert ALLOWED_ORIGIN == "http://localhost:5173"

# Frozen "now" for the fake wall clock; every seeded timestamp below is
# relative to this instant.
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    """Fresh tmp DB + fresh module-level router state for every test."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "stocks.db"))
    stocks_router._backoff = stocks_router._BackoffState()
    stocks_router._refresh_locks = {}
    yield


class _FakeClock:
    """Monotonic clock stand-in so backoff windows are stepped, not slept."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeWallClock:
    """`_utcnow` stand-in so freshness windows are stepped, not slept."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(stocks_router, "_monotonic", clock)
    return clock


@pytest.fixture()
def fake_utcnow(monkeypatch):
    clock = _FakeWallClock()
    monkeypatch.setattr(stocks_router, "_utcnow", clock)
    return clock


@pytest.fixture()
def client():
    return TestClient(app)


def _bar(ts: str, price: float) -> Bar:
    analytics = json.dumps(
        {"o": price, "h": price, "l": price, "c": price, "v": 1000, "vw": price, "n": 10}
    )
    return Bar(ts=ts, price=price, analytics=analytics)


def _summary(
    ticker: str, *, price=None, previous_close=None, is_stale=False, error=None
) -> StockSummary:
    info = TICKERS_BY_SYMBOL[ticker]
    change = None
    change_percent = None
    if price is not None and previous_close is not None and previous_close != 0:
        change = price - previous_close
        change_percent = (change / previous_close) * 100
    return StockSummary(
        ticker=ticker,
        name=info.name,
        sector=info.sector,
        price=price,
        currency="USD",
        previous_close=previous_close,
        change=change,
        change_percent=change_percent,
        is_stale=is_stale,
        error=error,
    )


def _all_success_summaries() -> dict[str, StockSummary]:
    return {
        ticker: _summary(ticker, price=100.0 + i, previous_close=100.0)
        for i, ticker in enumerate(TICKER_SYMBOLS)
    }


def _minute_bars_for(tickers) -> dict[str, Bar]:
    return {
        ticker: _bar(_iso(NOW - timedelta(minutes=1)), 100.0 + i)
        for i, ticker in enumerate(tickers)
    }


def _success_batch() -> tuple[dict[str, StockSummary], dict[str, Bar]]:
    """What `alpaca_client.fetch_summaries` returns on a fully good refresh."""
    return _all_success_summaries(), _minute_bars_for(TICKER_SYMBOLS)


def _all_failed_summaries(
    error: str = "alpaca rate limited: too many requests",
) -> dict[str, StockSummary]:
    """A batch in which *every* ticker failed -- what a 429 storm produces."""
    return {
        ticker: _summary(ticker, is_stale=True, error=error)
        for ticker in TICKER_SYMBOLS
    }


def _failed_batch(
    error: str = "alpaca rate limited: too many requests",
) -> tuple[dict[str, StockSummary], dict[str, Bar]]:
    """`fetch_summaries` never raises: a total failure is an all-error
    batch with no minute bars to persist."""
    return _all_failed_summaries(error), {}


def _seed_minute_rows(ticker: str, bars: list[Bar], recorded_at: str) -> None:
    storage.init_db()
    storage.upsert_bars("minute", ticker, bars, recorded_at)


def _prime_stale(age_seconds: float = CACHE_TTL_SECONDS + 5) -> dict[str, StockSummary]:
    """Seed a prior successful batch via the `summaries` table, stamped
    `age_seconds` in the past (default: outside the TTL, so the next
    request attempts a refresh)."""
    good = _all_success_summaries()
    fetched_at = _iso(
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    )
    storage.upsert_summaries(stocks_router._summary_rows(good), fetched_at)
    return good


def _summaries_stamp() -> str | None:
    return storage.last_fetch_at(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY)


def _table_rows(table: str) -> list[tuple]:
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return conn.execute(
            f"SELECT ticker, price, analytics, ts, recorded_at FROM {table} ORDER BY ticker, ts"
        ).fetchall()
    finally:
        conn.close()


def _bars_call(call) -> tuple[str, str, datetime]:
    """Extract (symbol, timeframe, start) from a fetch_bars mock call,
    tolerating positional or keyword style."""
    args = list(call.args)
    kwargs = dict(call.kwargs)

    def take(name: str):
        if args:
            return args.pop(0)
        return kwargs[name]

    return take("symbol"), take("timeframe"), take("start")


class TestListStocks:
    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_all_succeed_returns_200_with_all_20_tickers(self, mock_fetch, client):
        mock_fetch.return_value = _success_batch()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert "stocks" in body and "updated_at" in body
        returned_tickers = {s["ticker"] for s in body["stocks"]}
        assert returned_tickers == set(TICKER_SYMBOLS)
        assert len(body["stocks"]) == 20
        for stock in body["stocks"]:
            assert stock["is_stale"] is False
            assert stock["error"] is None
            assert stock["currency"] == "USD"

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_partial_failure_is_200_not_500_with_flagged_entries(
        self, mock_fetch, client
    ):
        summaries = _all_success_summaries()
        failing_ticker = "NFLX"
        summaries[failing_ticker] = _summary(
            failing_ticker, is_stale=True, error="alpaca error: symbol missing"
        )
        minute_bars = _minute_bars_for(
            [t for t in TICKER_SYMBOLS if t != failing_ticker]
        )
        mock_fetch.return_value = (summaries, minute_bars)

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        by_ticker = {s["ticker"]: s for s in body["stocks"]}
        failed = by_ticker[failing_ticker]
        assert failed["is_stale"] is True
        assert failed["error"] is not None
        assert failed["price"] is None
        healthy = by_ticker["AAPL"]
        assert healthy["is_stale"] is False
        assert healthy["error"] is None

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_successful_batch_persists_minute_bars_to_db(self, mock_fetch, client):
        mock_fetch.return_value = _success_batch()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        rows = _table_rows("bars_minute")
        assert {r[0] for r in rows} == set(TICKER_SYMBOLS)
        by_ticker = {r[0]: r for r in rows}
        aapl = by_ticker["AAPL"]
        # Row shape: ticker, price, analytics(JSON), ts, recorded_at.
        assert aapl[1] == pytest.approx(100.0)
        assert set(json.loads(aapl[2])) == {"o", "h", "l", "c", "v", "vw", "n"}
        assert aapl[3] == _iso(NOW - timedelta(minutes=1))
        assert aapl[4]  # recorded_at stamped

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_repeated_identical_minute_bar_does_not_bump_recorded_at(
        self, mock_fetch, client, fake_utcnow
    ):
        # The leaderboard poll upserts each ticker's latest minute bar every
        # ~20s. While that bar is unchanged its `recorded_at` must stay put;
        # a price change on the same (ticker, ts) restamps it.
        mock_fetch.return_value = _success_batch()
        client.get("/api/stocks")
        first = {r[0]: r for r in _table_rows("bars_minute")}["AAPL"]
        assert first[4] == _iso(NOW)

        fake_utcnow.advance(CACHE_TTL_SECONDS + 1)
        mock_fetch.return_value = _success_batch()  # same ts, same prices
        client.get("/api/stocks")
        assert mock_fetch.call_count == 2
        second = {r[0]: r for r in _table_rows("bars_minute")}["AAPL"]
        assert second[4] == _iso(NOW)  # unchanged

        fake_utcnow.advance(CACHE_TTL_SECONDS + 1)
        summaries, minute_bars = _success_batch()
        moved = minute_bars["AAPL"]
        minute_bars["AAPL"] = Bar(ts=moved.ts, price=moved.price + 1.0, analytics=moved.analytics)
        mock_fetch.return_value = (summaries, minute_bars)
        client.get("/api/stocks")
        third = {r[0]: r for r in _table_rows("bars_minute")}["AAPL"]
        assert third[1] == pytest.approx(moved.price + 1.0)
        assert third[4] == _iso(fake_utcnow.now)

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_partial_failure_persists_only_good_tickers_bars(
        self, mock_fetch, client
    ):
        summaries = _all_success_summaries()
        summaries["NFLX"] = _summary(
            "NFLX", is_stale=True, error="alpaca error: symbol missing"
        )
        minute_bars = _minute_bars_for([t for t in TICKER_SYMBOLS if t != "NFLX"])
        mock_fetch.return_value = (summaries, minute_bars)

        client.get("/api/stocks")

        stored = {r[0] for r in _table_rows("bars_minute")}
        assert "NFLX" not in stored
        assert "AAPL" in stored

    @patch("app.routers.stocks.storage.upsert_bars")
    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_db_write_failure_does_not_fail_endpoint(
        self, mock_fetch, mock_upsert, client
    ):
        # Persistence is best-effort: a broken disk must not break the
        # endpoint that just fetched perfectly good data.
        mock_fetch.return_value = _success_batch()
        mock_upsert.side_effect = sqlite3.OperationalError("disk I/O error")

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert all(s["error"] is None for s in body["stocks"])


class TestGetStock:
    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_valid_ticker_returns_200_with_expected_shape(self, mock_fetch, client):
        mock_fetch.return_value = _success_batch()

        response = client.get("/api/stocks/AAPL")

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AAPL"
        assert body["name"] == "Apple"
        assert body["sector"] == "Technology"
        for key in (
            "ticker",
            "name",
            "sector",
            "price",
            "currency",
            "previous_close",
            "change",
            "change_percent",
            "is_stale",
            "error",
        ):
            assert key in body

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_dotted_ticker_resolves(self, mock_fetch, client):
        # BRK.B contains a dot; it must route to the {ticker} handler, not
        # 404 or get mangled by path matching.
        mock_fetch.return_value = _success_batch()

        response = client.get("/api/stocks/BRK.B")

        assert response.status_code == 200
        assert response.json()["ticker"] == "BRK.B"

    def test_unknown_ticker_returns_404(self, client):
        response = client.get("/api/stocks/FAKE")

        assert response.status_code == 404


class TestTotalFailureServesStale:
    """A fully rate-limited batch must not blank the page: falls back to
    the `summaries` table (marked stale), then the error-flagged batch
    itself."""

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_total_failure_serves_last_known_good_batch_marked_stale(
        self, mock_fetch, client
    ):
        good = _prime_stale()
        mock_fetch.return_value = _failed_batch()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        by_ticker = {s["ticker"]: s for s in body["stocks"]}
        # Last-known-good prices survive the failed refresh...
        assert by_ticker["AAPL"]["price"] == pytest.approx(good["AAPL"].price)
        assert by_ticker["NFLX"]["price"] == pytest.approx(good["NFLX"].price)
        for stock in body["stocks"]:
            assert stock["is_stale"] is True

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_failed_batch_is_not_stored_and_does_not_clobber_table(
        self, mock_fetch, client
    ):
        good = _prime_stale()
        stamp_before = _summaries_stamp()
        mock_fetch.return_value = _failed_batch()

        client.get("/api/stocks")

        # The all-error batch must not have been written anywhere: this is
        # the exact bug that blanked the page. Table rows AND the fetch_log
        # stamp are untouched (so the next poll retries once backoff lifts).
        surviving = storage.get_summaries()
        assert len(surviving) == 20
        assert surviving["AAPL"].price == pytest.approx(good["AAPL"].price)
        assert surviving["AAPL"].error is None
        assert _summaries_stamp() == stamp_before

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_total_failure_with_cold_memory_serves_summaries_table(
        self, mock_fetch, client
    ):
        # This process has never fetched, but a previous process wrote the
        # `summaries` table: the DB is the backup and must back the
        # leaderboard, with price AND previous_close/change intact, stale.
        good = _prime_stale(age_seconds=3600)
        mock_fetch.return_value = _failed_batch()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        by_ticker = {s["ticker"]: s for s in body["stocks"]}
        aapl = by_ticker["AAPL"]
        assert aapl["price"] == pytest.approx(good["AAPL"].price)
        assert aapl["previous_close"] == pytest.approx(good["AAPL"].previous_close)
        assert aapl["change"] == pytest.approx(good["AAPL"].change)
        assert aapl["change_percent"] == pytest.approx(good["AAPL"].change_percent)
        assert aapl["is_stale"] is True
        assert aapl["error"] is None
        assert mock_fetch.call_count == 1

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_cold_start_total_failure_returns_200_error_flagged_batch(
        self, mock_fetch, client
    ):
        # Nothing has ever succeeded and the DB is empty: keep the
        # existing behavior (200 with error-flagged rows, which the
        # frontend greys out) rather than 500.
        mock_fetch.return_value = _failed_batch()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        for stock in body["stocks"]:
            assert stock["is_stale"] is True
            assert stock["error"] is not None
            assert stock["price"] is None

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_single_ticker_endpoint_also_serves_stale_on_total_failure(
        self, mock_fetch, client
    ):
        good = _prime_stale()
        mock_fetch.return_value = _failed_batch()

        response = client.get("/api/stocks/AAPL")

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AAPL"
        assert body["price"] == pytest.approx(good["AAPL"].price)
        assert body["is_stale"] is True

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_partial_failure_is_still_stored_as_success(self, mock_fetch, client):
        # Only a *total* failure counts as "no fresh data"; partial success
        # is stored + stamped normally and must not trip the backoff.
        summaries = _all_success_summaries()
        summaries["NFLX"] = _summary(
            "NFLX", is_stale=True, error="alpaca error: boom"
        )
        mock_fetch.return_value = (
            summaries,
            _minute_bars_for([t for t in TICKER_SYMBOLS if t != "NFLX"]),
        )

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert _summaries_stamp() is not None
        stored = storage.get_summaries()
        assert len(stored) == 20
        assert stored["NFLX"].error == "alpaca error: boom"
        assert stored["AAPL"].error is None
        assert stocks_router._backoff.consecutive_failures == 0


class TestRateLimitBackoff:
    """Back off between refresh attempts instead of hammering Alpaca.
    Timing is driven by `fake_clock`; nothing sleeps."""

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_repeat_requests_during_backoff_window_do_not_hit_alpaca(
        self, mock_fetch, client, fake_clock
    ):
        _prime_stale()
        mock_fetch.return_value = _failed_batch()

        first = client.get("/api/stocks")
        assert first.status_code == 200
        assert mock_fetch.call_count == 1

        # Poll repeatedly, well inside the backoff window: each request is
        # served from stale with no further Alpaca traffic at all.
        for _ in range(3):
            fake_clock.advance(20.0)
            response = client.get("/api/stocks")
            assert response.status_code == 200
            assert all(s["is_stale"] is True for s in response.json()["stocks"])
        assert mock_fetch.call_count == 1

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_retry_allowed_once_backoff_window_expires(
        self, mock_fetch, client, fake_clock
    ):
        _prime_stale()
        mock_fetch.return_value = _failed_batch()

        client.get("/api/stocks")
        assert mock_fetch.call_count == 1

        fake_clock.advance(stocks_router.BACKOFF_BASE_SECONDS + 1)
        client.get("/api/stocks")
        assert mock_fetch.call_count == 2

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_backoff_doubles_per_failure_and_is_capped(
        self, mock_fetch, client, fake_clock, monkeypatch
    ):
        monkeypatch.setattr(stocks_router, "BACKOFF_BASE_SECONDS", 10.0)
        monkeypatch.setattr(stocks_router, "BACKOFF_MAX_SECONDS", 40.0)
        _prime_stale()
        mock_fetch.return_value = _failed_batch()

        observed = []
        for _ in range(4):
            client.get("/api/stocks")
            observed.append(stocks_router._backoff.blocked_until - fake_clock.now)
            # Step past the window so the next request is allowed to retry.
            fake_clock.advance(observed[-1] + 1)

        assert observed == [10.0, 20.0, 40.0, 40.0]
        assert mock_fetch.call_count == 4

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_successful_refresh_resets_backoff(self, mock_fetch, client, fake_clock):
        _prime_stale()
        mock_fetch.return_value = _failed_batch()
        client.get("/api/stocks")
        assert stocks_router._backoff.consecutive_failures == 1

        fake_clock.advance(stocks_router.BACKOFF_BASE_SECONDS + 1)
        mock_fetch.return_value = _success_batch()
        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert all(s["is_stale"] is False for s in response.json()["stocks"])
        assert stocks_router._backoff.consecutive_failures == 0
        assert stocks_router._backoff.blocked_until == 0.0

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_fresh_table_read_during_backoff_window_does_not_reset_backoff(
        self, mock_fetch, client, fake_clock
    ):
        # A fresh-table read performs no fetch; it must not be mistaken for
        # a refresh and clear backoff (TTL is shorter than max backoff).
        mock_fetch.return_value = _success_batch()
        client.get("/api/stocks")
        stocks_router._backoff.record_failure(_all_failed_summaries())
        failures_before = stocks_router._backoff.consecutive_failures

        client.get("/api/stocks")

        assert stocks_router._backoff.consecutive_failures == failures_before

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_cold_start_backoff_serves_last_failed_batch_without_refetching(
        self, mock_fetch, client, fake_clock
    ):
        # Nothing known-good exists anywhere (memory and DB both empty), so
        # the error-flagged batch is all we have -- but we still must not
        # re-hit Alpaca inside the window.
        mock_fetch.return_value = _failed_batch()

        first = client.get("/api/stocks")
        assert first.status_code == 200
        assert mock_fetch.call_count == 1

        fake_clock.advance(1.0)
        second = client.get("/api/stocks")

        assert second.status_code == 200
        assert len(second.json()["stocks"]) == 20
        assert mock_fetch.call_count == 1


class TestGetStockHistory:
    """SQLite-first history flow: the DB is the cache AND the backup."""

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_cold_start_fetches_alpaca_and_stores(
        self, mock_fetch, client, fake_utcnow
    ):
        bars = [
            _bar(_iso(NOW - timedelta(minutes=3)), 100.0),
            _bar(_iso(NOW - timedelta(minutes=2)), 101.0),
            _bar(_iso(NOW - timedelta(minutes=1)), 102.0),
        ]
        mock_fetch.return_value = bars

        response = client.get("/api/stocks/AAPL/history")

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AAPL"
        assert body["range"] == "1d"
        assert body["interval"] == "1m"
        assert body["is_stale"] is False
        assert body["error"] is None
        assert [p["close"] for p in body["points"]] == pytest.approx(
            [100.0, 101.0, 102.0]
        )
        assert [p["t"] for p in body["points"]] == [b.ts for b in bars]

        # Exactly one Alpaca call, for the minute timeframe over 24h.
        assert mock_fetch.call_count == 1
        symbol, timeframe, start = _bars_call(mock_fetch.call_args)
        assert symbol == "AAPL"
        assert timeframe == "1Min"
        assert start == NOW - timedelta(hours=24)

        # The bars landed in the minute table (and only there).
        rows = _table_rows("bars_minute")
        assert [(r[0], r[3], r[1]) for r in rows] == [
            ("AAPL", b.ts, b.price) for b in bars
        ]
        assert _table_rows("bars_hour") == []
        assert _table_rows("bars_days") == []

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_warm_db_within_freshness_serves_without_alpaca(
        self, mock_fetch, client, fake_utcnow
    ):
        bars = [_bar(_iso(NOW - timedelta(minutes=1)), 102.0)]
        mock_fetch.return_value = bars

        first = client.get("/api/stocks/AAPL/history")
        second = client.get("/api/stocks/AAPL/history")

        assert first.status_code == 200
        assert second.status_code == 200
        assert mock_fetch.call_count == 1, "second request must be served from SQLite"
        assert second.json()["points"] == first.json()["points"]
        assert second.json()["is_stale"] is False

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_stale_db_refetches_and_does_not_duplicate_rows(
        self, mock_fetch, client, fake_utcnow
    ):
        overlap_ts = _iso(NOW - timedelta(minutes=1))
        mock_fetch.return_value = [_bar(overlap_ts, 102.0)]
        client.get("/api/stocks/AAPL/history")

        # Step past the minute tier's freshness window: the DB is now
        # considered stale and Alpaca must be consulted again.
        fake_utcnow.advance(FRESHNESS_MINUTE_SECONDS + 60)
        new_ts = _iso(fake_utcnow.now - timedelta(minutes=1))
        mock_fetch.return_value = [
            _bar(overlap_ts, 102.5),  # same bar, revised price
            _bar(new_ts, 103.0),
        ]
        response = client.get("/api/stocks/AAPL/history")

        assert mock_fetch.call_count == 2
        assert response.status_code == 200
        rows = _table_rows("bars_minute")
        # Upsert, not append: one row per (ticker, ts), overlap updated.
        assert [(r[3], r[1]) for r in rows] == [(overlap_ts, 102.5), (new_ts, 103.0)]

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_alpaca_down_with_db_data_serves_stale_200(
        self, mock_fetch, client, fake_utcnow
    ):
        seeded = [
            _bar(_iso(NOW - timedelta(hours=2)), 97.0),
            _bar(_iso(NOW - timedelta(hours=1)), 98.5),
        ]
        # recorded_at is well outside the freshness window, so a refetch
        # is attempted -- and fails.
        _seed_minute_rows("AAPL", seeded, _iso(NOW - timedelta(hours=1)))
        mock_fetch.side_effect = AlpacaError("alpaca error: connection refused")

        response = client.get("/api/stocks/AAPL/history")

        assert response.status_code == 200
        body = response.json()
        assert body["is_stale"] is True
        assert body["error"] is not None
        assert [p["close"] for p in body["points"]] == pytest.approx([97.0, 98.5])

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_alpaca_down_with_empty_db_returns_503(
        self, mock_fetch, client, fake_utcnow
    ):
        mock_fetch.side_effect = AlpacaError("alpaca error: connection refused")

        response = client.get("/api/stocks/AAPL/history")

        assert response.status_code == 503

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_empty_fetch_is_success_not_outage_and_counts_as_fresh(
        self, mock_fetch, client, fake_utcnow
    ):
        # Weekend/market closed: Alpaca returns 200 with no bars -- a
        # *successful* fetch (no stale flag). Freshness for the next poll
        # can't come from recorded_at since nothing was stored.
        mock_fetch.return_value = []

        first = client.get("/api/stocks/AAPL/history")
        second = client.get("/api/stocks/AAPL/history")

        assert first.status_code == 200
        body = first.json()
        assert body["points"] == []
        assert body["is_stale"] is False
        assert body["error"] is None
        assert mock_fetch.call_count == 1, "empty fetch must still count as fresh"
        assert second.status_code == 200

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_leaderboard_minute_bar_writes_do_not_suppress_intraday_backfill(
        self, mock_summaries, mock_fetch, client, fake_utcnow
    ):
        # Regression: the leaderboard poll's minute-bar upsert must NOT
        # make the minute tier look "fresh" -- a cold 1d history request
        # right after a leaderboard poll still has to backfill from Alpaca.
        latest_ts = _iso(NOW - timedelta(minutes=1))
        mock_summaries.return_value = (
            _success_batch()[0],
            {"AAPL": _bar(latest_ts, 102.0)},
        )
        assert client.get("/api/stocks").status_code == 200
        assert _table_rows("bars_minute") != []  # leaderboard wrote a bar

        backfill = [
            _bar(_iso(NOW - timedelta(minutes=3)), 100.0),
            _bar(_iso(NOW - timedelta(minutes=2)), 101.0),
            _bar(latest_ts, 102.0),
        ]
        mock_fetch.return_value = backfill

        response = client.get("/api/stocks/AAPL/history")

        assert response.status_code == 200
        assert mock_fetch.call_count == 1, "intraday backfill must run"
        assert [p["close"] for p in response.json()["points"]] == pytest.approx(
            [100.0, 101.0, 102.0]
        )

        # ...and the backfill itself is what now counts as fresh.
        client.get("/api/stocks/AAPL/history")
        assert mock_fetch.call_count == 1

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_freshness_survives_a_restart_via_the_db_fetch_log(
        self, mock_fetch, client, fake_utcnow
    ):
        mock_fetch.return_value = [_bar(_iso(NOW - timedelta(minutes=1)), 102.0)]
        client.get("/api/stocks/AAPL/history")
        assert mock_fetch.call_count == 1

        # Simulate a process restart: all in-memory router state is gone,
        # the SQLite file is not.
        stocks_router._refresh_locks = {}

        client.get("/api/stocks/AAPL/history")
        assert mock_fetch.call_count == 1, "fetch stamp must come from SQLite"

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_range_30d_uses_hour_tier(self, mock_fetch, client, fake_utcnow):
        bars = [_bar(_iso(NOW - timedelta(days=2)), 99.0)]
        mock_fetch.return_value = bars

        response = client.get("/api/stocks/AAPL/history?range=30d")

        assert response.status_code == 200
        body = response.json()
        assert body["range"] == "30d"
        assert body["interval"] == "1h"
        symbol, timeframe, start = _bars_call(mock_fetch.call_args)
        assert (symbol, timeframe) == ("AAPL", "1Hour")
        assert start == NOW - timedelta(days=30)
        assert [r[0] for r in _table_rows("bars_hour")] == ["AAPL"]
        assert _table_rows("bars_minute") == []

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_range_all_uses_days_tier(self, mock_fetch, client, fake_utcnow):
        bars = [
            _bar("2026-06-01T04:00:00Z", 90.0),
            _bar("2026-07-01T04:00:00Z", 95.0),
        ]
        mock_fetch.return_value = bars

        response = client.get("/api/stocks/AAPL/history?range=all")

        assert response.status_code == 200
        body = response.json()
        assert body["range"] == "all"
        assert body["interval"] == "1d"
        symbol, timeframe, start = _bars_call(mock_fetch.call_args)
        assert (symbol, timeframe) == ("AAPL", "1Day")
        assert start == NOW - timedelta(days=365)
        assert [r[0] for r in _table_rows("bars_days")] == ["AAPL", "AAPL"]

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_range_all_serves_every_stored_daily_bar_not_just_the_backfill_window(
        self, mock_fetch, client, fake_utcnow
    ):
        # The all-time view grows with the store: daily bars older than the
        # 365d backfill window must still be served -- the daily tier's read
        # is unbounded and never pruned. Only the *fetch* is bounded.
        ancient = _iso(NOW - timedelta(days=3 * 365))
        storage.upsert_bars("days", "AAPL", [_bar(ancient, 42.0)], _iso(NOW))
        mock_fetch.return_value = [_bar("2026-08-19T04:00:00Z", 95.0)]

        response = client.get("/api/stocks/AAPL/history?range=all")

        assert response.status_code == 200
        points = response.json()["points"]
        assert [p["t"] for p in points] == [ancient, "2026-08-19T04:00:00Z"]
        assert [p["close"] for p in points] == pytest.approx([42.0, 95.0])
        # The Alpaca request reaches back to the oldest stored daily bar
        # (see TestNoStragglerDailyBars), so that row is re-checked too.
        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(days=3 * 365)

        # Fresh read (no second fetch) serves the same unbounded series, and
        # so does the stale-on-error path.
        again = client.get("/api/stocks/AAPL/history?range=all")
        assert mock_fetch.call_count == 1
        assert [p["close"] for p in again.json()["points"]] == pytest.approx([42.0, 95.0])

        fake_utcnow.advance(config.FRESHNESS_DAY_SECONDS + 1)
        mock_fetch.side_effect = AlpacaError("alpaca error: down")
        stale = client.get("/api/stocks/AAPL/history?range=all")
        assert stale.status_code == 200
        assert stale.json()["is_stale"] is True
        assert [p["close"] for p in stale.json()["points"]] == pytest.approx([42.0, 95.0])

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_minute_and_hour_reads_stay_windowed(self, mock_fetch, client, fake_utcnow):
        # Unlike the daily tier, the pruned tiers are read over their
        # retention window only (an ancient row that somehow escaped
        # pruning must not leak into the 1d series).
        ancient = _iso(NOW - timedelta(days=3))
        storage.upsert_bars("minute", "AAPL", [_bar(ancient, 1.0)], _iso(NOW))
        mock_fetch.return_value = [_bar(_iso(NOW - timedelta(minutes=1)), 102.0)]

        response = client.get("/api/stocks/AAPL/history?range=1d")

        assert [p["close"] for p in response.json()["points"]] == pytest.approx([102.0])

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_tiers_are_independent_for_same_ticker(
        self, mock_fetch, client, fake_utcnow
    ):
        # A fresh 1d tier must not satisfy an all-time request (different table,
        # different timeframe, separate freshness/locks) -- and vice versa.
        def by_timeframe(*args, **kwargs):
            timeframe = kwargs.get("timeframe", args[1] if len(args) > 1 else None)
            if timeframe == "1Min":
                return [_bar(_iso(NOW - timedelta(minutes=1)), 102.0)]
            assert timeframe == "1Day"
            return [_bar("2026-07-01T04:00:00Z", 95.0)]

        mock_fetch.side_effect = by_timeframe

        day = client.get("/api/stocks/AAPL/history?range=1d")
        year = client.get("/api/stocks/AAPL/history?range=all")
        day_again = client.get("/api/stocks/AAPL/history?range=1d")

        assert day.status_code == 200
        assert year.status_code == 200
        assert mock_fetch.call_count == 2, "one fetch per tier, then both fresh"
        assert day_again.status_code == 200
        assert mock_fetch.call_count == 2
        assert [p["close"] for p in year.json()["points"]] == pytest.approx([95.0])
        assert [p["close"] for p in day.json()["points"]] == pytest.approx([102.0])

    def test_unknown_ticker_returns_404(self, client):
        response = client.get("/api/stocks/FAKE/history")

        assert response.status_code == 404

    def test_unknown_range_returns_422(self, client):
        response = client.get("/api/stocks/AAPL/history?range=5y")

        assert response.status_code == 422

    def test_legacy_1y_range_is_gone(self, client):
        # `1y` was replaced by the all-time `all` view.
        assert client.get("/api/stocks/AAPL/history?range=1y").status_code == 422

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    async def test_concurrent_requests_collapse_into_one_fetch(
        self, mock_fetch, fake_utcnow
    ):
        # Two simultaneous requests for the same (ticker, tier) share a
        # single Alpaca fetch: the sleep keeps the first in flight so the
        # second blocks on the per-key lock, then finds the DB fresh.
        def slow_fetch(*args, **kwargs):
            time.sleep(0.2)
            return [_bar(_iso(NOW - timedelta(minutes=1)), 102.0)]

        mock_fetch.side_effect = slow_fetch

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as async_client:
            first, second = await asyncio.gather(
                async_client.get("/api/stocks/AAPL/history"),
                async_client.get("/api/stocks/AAPL/history"),
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert mock_fetch.call_count == 1
        assert first.json()["points"] == second.json()["points"]


class TestNoStragglerDailyBars:
    """The daily tier's fetch reaches back to the oldest stored row, so rows
    outside the 365-day window are still re-checked (and re-adjusted after
    a split). Minute/hour keep fixed windows."""

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_days_fetch_starts_at_oldest_stored_row_when_older_than_window(
        self, mock_fetch, client, fake_utcnow
    ):
        ancient = _iso(NOW - timedelta(days=3 * 365))
        old_stamp = _iso(NOW - timedelta(days=400))
        storage.upsert_bars("days", "AAPL", [_bar(ancient, 42.0)], old_stamp)
        mock_fetch.return_value = [
            _bar(ancient, 42.0),  # unchanged
            _bar("2026-08-19T04:00:00Z", 95.0),
        ]

        response = client.get("/api/stocks/AAPL/history?range=all")

        assert response.status_code == 200
        symbol, timeframe, start = _bars_call(mock_fetch.call_args)
        assert (symbol, timeframe) == ("AAPL", "1Day")
        assert start == NOW - timedelta(days=3 * 365)
        by_ts = {r[3]: r for r in _table_rows("bars_days")}
        assert by_ts[ancient][4] == old_stamp  # same price -> stamp untouched
        assert by_ts["2026-08-19T04:00:00Z"][4] == _iso(NOW)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_split_adjusted_old_row_is_rewritten_and_restamped(
        self, mock_fetch, client, fake_utcnow
    ):
        ancient = _iso(NOW - timedelta(days=3 * 365))
        old_stamp = _iso(NOW - timedelta(days=400))
        storage.upsert_bars("days", "AAPL", [_bar(ancient, 400.0)], old_stamp)
        mock_fetch.return_value = [_bar(ancient, 100.0)]  # 4:1 split adjusted

        client.get("/api/stocks/AAPL/history?range=all")

        rows = _table_rows("bars_days")
        assert len(rows) == 1
        assert rows[0][1] == pytest.approx(100.0)
        assert rows[0][4] == _iso(NOW)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_days_fetch_keeps_window_when_oldest_row_is_inside_it(
        self, mock_fetch, client, fake_utcnow
    ):
        recent = _iso(NOW - timedelta(days=30))
        storage.upsert_bars("days", "AAPL", [_bar(recent, 42.0)], _iso(NOW))
        mock_fetch.return_value = []

        client.get("/api/stocks/AAPL/history?range=all")

        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(days=365)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_days_fetch_keeps_window_when_nothing_is_stored(
        self, mock_fetch, client, fake_utcnow
    ):
        mock_fetch.return_value = []

        client.get("/api/stocks/AAPL/history?range=all")

        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(days=365)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_unparseable_oldest_ts_falls_back_to_window(
        self, mock_fetch, client, fake_utcnow
    ):
        storage.upsert_bars("days", "AAPL", [_bar("garbage", 1.0)], _iso(NOW))
        mock_fetch.return_value = []

        response = client.get("/api/stocks/AAPL/history?range=all")

        assert response.status_code == 200
        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(days=365)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_oldest_row_of_another_ticker_does_not_widen_the_fetch(
        self, mock_fetch, client, fake_utcnow
    ):
        ancient = _iso(NOW - timedelta(days=3 * 365))
        storage.upsert_bars("days", "MSFT", [_bar(ancient, 42.0)], _iso(NOW))
        mock_fetch.return_value = []

        client.get("/api/stocks/AAPL/history?range=all")

        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(days=365)

    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_minute_and_hour_fetches_keep_fixed_windows_despite_ancient_rows(
        self, mock_fetch, client, fake_utcnow
    ):
        ancient = _iso(NOW - timedelta(days=3 * 365))
        storage.upsert_bars("minute", "AAPL", [_bar(ancient, 1.0)], _iso(NOW))
        storage.upsert_bars("hour", "AAPL", [_bar(ancient, 1.0)], _iso(NOW))
        mock_fetch.return_value = []

        client.get("/api/stocks/AAPL/history?range=1d")
        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(hours=24)

        client.get("/api/stocks/AAPL/history?range=30d")
        _, _, start = _bars_call(mock_fetch.call_args)
        assert start == NOW - timedelta(days=30)


class TestGetStoredData:
    """`GET /api/stocks/{ticker}/stored?tier=` -- raw SQLite inspection."""

    def test_unknown_ticker_returns_404(self, client):
        assert client.get("/api/stocks/FAKE/stored").status_code == 404

    def test_unknown_tier_returns_422(self, client):
        # "month" is the daily tier's pre-rename name: rejecting it doubles
        # as a guard that the old value is really gone from the API.
        assert client.get("/api/stocks/AAPL/stored?tier=month").status_code == 422

    def test_empty_store_is_200_with_no_rows(self, client):
        response = client.get("/api/stocks/AAPL/stored?tier=days")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "ticker": "AAPL",
            "tier": "days",
            "table": "bars_days",
            "currency": "USD",
            "last_fetch_at": None,
            "counts": {"minute": 0, "hour": 0, "days": 0},
            "rows": [],
        }

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    @patch("app.routers.stocks.alpaca_client.fetch_bars")
    def test_returns_every_stored_column_oldest_first_and_never_calls_alpaca(
        self, mock_bars, mock_summaries, client
    ):
        older = _iso(NOW - timedelta(minutes=2))
        newer = _iso(NOW - timedelta(minutes=1))
        # Upsert out of order to prove the response is sorted by ts.
        storage.upsert_bars("minute", "AAPL", [_bar(newer, 101.0)], _iso(NOW))
        storage.upsert_bars("minute", "AAPL", [_bar(older, 100.0)], _iso(NOW))
        storage.upsert_bars("hour", "AAPL", [_bar(older, 99.0)], _iso(NOW))
        storage.upsert_bars("minute", "MSFT", [_bar(older, 300.0)], _iso(NOW))
        storage.record_fetch("minute", "AAPL", _iso(NOW))

        response = client.get("/api/stocks/AAPL/stored?tier=minute")

        assert response.status_code == 200
        body = response.json()
        assert body["tier"] == "minute"
        assert body["table"] == "bars_minute"
        assert body["last_fetch_at"] == _iso(NOW)
        assert body["counts"] == {"minute": 2, "hour": 1, "days": 0}
        assert [r["ts"] for r in body["rows"]] == [older, newer]
        first = body["rows"][0]
        assert first["price"] == pytest.approx(100.0)
        assert first["recorded_at"] == _iso(NOW)
        assert first["analytics"] == {
            "o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 1000, "vw": 100.0, "n": 10,
        }
        # Other tickers never leak in, and nothing went near Alpaca.
        assert all(r["price"] != 300.0 for r in body["rows"])
        mock_bars.assert_not_called()
        mock_summaries.assert_not_called()

    def test_defaults_to_the_minute_tier(self, client):
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))

        body = client.get("/api/stocks/AAPL/stored").json()

        assert body["tier"] == "minute"
        assert len(body["rows"]) == 1

    def test_unreadable_analytics_is_surfaced_raw_not_hidden(self, client):
        storage.upsert_bars(
            "hour", "AAPL", [Bar(ts=_iso(NOW), price=5.0, analytics="not json")], _iso(NOW)
        )

        body = client.get("/api/stocks/AAPL/stored?tier=hour").json()

        assert body["rows"][0]["analytics"] == {"raw": "not json"}


class TestCors:
    def test_preflight_options_allows_configured_origin(self, client):
        response = client.options(
            "/api/stocks",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

    @patch("app.routers.stocks.alpaca_client.fetch_summaries")
    def test_actual_get_request_includes_allow_origin_header(self, mock_fetch, client):
        mock_fetch.return_value = _success_batch()

        response = client.get("/api/stocks", headers={"Origin": ALLOWED_ORIGIN})

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
