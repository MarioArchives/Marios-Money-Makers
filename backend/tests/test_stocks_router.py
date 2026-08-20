"""Tests for the `/api/stocks` HTTP layer (`app.routers.stocks` + `app.main`).

Uses FastAPI's `TestClient` against the real app; `app.yfinance_client` is
mocked at the call sites the router uses (`app.routers.stocks.yfinance_client`)
so no real network calls are made. Each test gets fresh, empty cache
instances (see `_reset_caches`) so tests never leak state between each
other via the module-level caches in `app.routers.stocks`.

These tests describe the *intended* router behavior once implemented; with 
the router still a skeleton (handlers `raise NotImplementedError`), they
are expected to fail/error for that reason, not because of import errors,
missing fixtures, or broken CORS wiring.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.routers.stocks as stocks_router
from app.cache import TTLCacheWithLock
from app.config import ALLOWED_ORIGINS, CACHE_TTL_SECONDS, HISTORY_CACHE_TTL_SECONDS
from app.main import app
from app.schemas import HistoryPoint, HistoryResponse, StockSummary
from app.tickers import TICKER_SYMBOLS, TICKERS_BY_SYMBOL

ALLOWED_ORIGIN = ALLOWED_ORIGINS[0]
assert ALLOWED_ORIGIN == "http://localhost:5173"


@pytest.fixture(autouse=True)
def _reset_caches():
    """Give each test fresh, empty caches instead of sharing module state.

    Constructing a new `TTLCacheWithLock` only touches its (implemented)
    `__init__`, so this works even while `cache.py`'s methods are still
    skeletons -- unlike calling `.invalidate()`, which would raise.

    Also resets the module-level rate-limit backoff bookkeeping (plan v3),
    which is in-process mutable state of exactly the same kind.
    """
    stocks_router._stocks_cache = TTLCacheWithLock(ttl=CACHE_TTL_SECONDS)
    stocks_router._history_cache = TTLCacheWithLock(ttl=HISTORY_CACHE_TTL_SECONDS)
    stocks_router._backoff = stocks_router._BackoffState()
    yield


class _FakeClock:
    """Monotonic clock stand-in so backoff windows are stepped, not slept."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(stocks_router, "_monotonic", clock)
    return clock


@pytest.fixture()
def client():
    return TestClient(app)


def _summary(ticker: str, *, price=None, previous_close=None, is_stale=False, error=None) -> StockSummary:
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
        currency="GBP",
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


def _all_failed_summaries(
    error: str = "yfinance rate limited: Too Many Requests.",
) -> dict[str, StockSummary]:
    """A batch in which *every* ticker failed -- what a 429 storm produces.

    `fetch_summaries` never raises, so this is exactly what the router sees
    when Yahoo rate-limits the whole refresh.
    """
    return {
        ticker: _summary(ticker, is_stale=True, error=error)
        for ticker in TICKER_SYMBOLS
    }


def _history(ticker: str, *, points=None, is_stale=False, error=None) -> HistoryResponse:
    return HistoryResponse(
        ticker=ticker,
        interval="5m",
        range="1d",
        points=points if points is not None else [],
        is_stale=is_stale,
        error=error,
    )


class TestListStocks:
    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_all_succeed_returns_200_with_all_20_tickers(self, mock_fetch, client):
        mock_fetch.return_value = _all_success_summaries()

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

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_partial_failure_is_200_not_500_with_flagged_entries(self, mock_fetch, client):
        summaries = _all_success_summaries()
        failing_ticker = "BP.L"
        summaries[failing_ticker] = _summary(
            failing_ticker, is_stale=True, error="yfinance error: network down"
        )
        mock_fetch.return_value = summaries

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        by_ticker = {s["ticker"]: s for s in body["stocks"]}
        failed = by_ticker[failing_ticker]
        assert failed["is_stale"] is True
        assert failed["error"] is not None
        assert failed["price"] is None
        # Other tickers are unaffected by the one failure.
        healthy = by_ticker["AZN.L"]
        assert healthy["is_stale"] is False
        assert healthy["error"] is None


class TestGetStock:
    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_valid_ticker_returns_200_with_expected_shape(self, mock_fetch, client):
        mock_fetch.return_value = _all_success_summaries()

        response = client.get("/api/stocks/AZN.L")

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AZN.L"
        assert body["name"] == "AstraZeneca"
        assert body["sector"] == "Pharmaceuticals"
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

    def test_unknown_ticker_returns_404(self, client):
        response = client.get("/api/stocks/FAKE.L")

        assert response.status_code == 404


class TestGetStockHistory:
    @patch("app.routers.stocks.yfinance_client.fetch_history")
    def test_valid_ticker_returns_200_with_points(self, mock_fetch, client):
        points = [
            HistoryPoint(t="2026-08-19T09:00:00+00:00", close=100.0),
            HistoryPoint(t="2026-08-19T09:05:00+00:00", close=101.5),
        ]
        mock_fetch.return_value = _history("AZN.L", points=points)

        response = client.get("/api/stocks/AZN.L/history")

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AZN.L"
        assert body["is_stale"] is False
        assert len(body["points"]) == 2
        assert body["points"][0]["close"] == pytest.approx(100.0)

    def test_unknown_ticker_returns_404(self, client):
        response = client.get("/api/stocks/FAKE.L/history")

        assert response.status_code == 404

    @patch("app.routers.stocks.yfinance_client.fetch_history")
    def test_fetch_failure_with_cached_data_returns_200_stale(self, mock_fetch, client):
        ticker = "AZN.L"
        cached_points = [
            HistoryPoint(t="2026-08-19T08:55:00+00:00", close=97.0),
            HistoryPoint(t="2026-08-19T09:00:00+00:00", close=98.5),
        ]
        cached_response = _history(ticker, points=cached_points)
        # Prime the stale shadow-store directly: `get_stale`/`get_or_fetch`
        # are themselves still skeletons in `cache.py`, but the internal
        # `_stale` dict they're documented to read from is real, settable
        # state -- this seeds "a fetch already succeeded once" without
        # depending on cache.py's unimplemented methods.
        stocks_router._history_cache._stale[ticker] = cached_response

        # A fresh fetch now fails; yfinance_client itself never raises for
        # a per-ticker failure, it returns an error result instead.
        mock_fetch.return_value = _history(
            ticker, points=[], is_stale=True, error="yfinance error: network down"
        )

        response = client.get(f"/api/stocks/{ticker}/history")

        assert response.status_code == 200
        body = response.json()
        assert body["is_stale"] is True
        assert len(body["points"]) == 2
        assert [p["close"] for p in body["points"]] == pytest.approx([97.0, 98.5])

    @patch("app.routers.stocks.yfinance_client.fetch_history")
    def test_fetch_failure_with_no_cache_returns_503(self, mock_fetch, client):
        ticker = "GSK.L"
        mock_fetch.return_value = _history(
            ticker, points=[], is_stale=True, error="yfinance error: network down"
        )

        response = client.get(f"/api/stocks/{ticker}/history")

        assert response.status_code == 503


class TestTotalFailureServesStale:
    """Plan v3: a fully rate-limited batch must not blank the page.

    `fetch_summaries` never raises, so an all-error batch previously looked
    like a *successful* fetch to `get_or_fetch` and was cached, clobbering
    the stale shadow store. These tests pin the corrected behavior, which
    mirrors the history endpoint's existing serve-stale pattern.
    """

    def _prime_stale(self) -> dict[str, StockSummary]:
        """Seed "a batch fetch already succeeded once" via the shadow store."""
        good = _all_success_summaries()
        stocks_router._stocks_cache._stale[stocks_router.STOCKS_CACHE_KEY] = good
        return good

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_total_failure_serves_last_known_good_batch_marked_stale(
        self, mock_fetch, client
    ):
        good = self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        by_ticker = {s["ticker"]: s for s in body["stocks"]}
        # Last-known-good prices survive the failed refresh...
        assert by_ticker["AZN.L"]["price"] == pytest.approx(good["AZN.L"].price)
        assert by_ticker["BP.L"]["price"] == pytest.approx(good["BP.L"].price)
        # ...and every row is flagged as not-fresh.
        for stock in body["stocks"]:
            assert stock["is_stale"] is True

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_failed_batch_is_not_cached_and_does_not_clobber_stale(
        self, mock_fetch, client
    ):
        good = self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()

        client.get("/api/stocks")

        # The all-error batch must not have been stored anywhere: this is
        # the exact bug that blanked the page.
        assert stocks_router.STOCKS_CACHE_KEY not in stocks_router._stocks_cache._cache
        surviving = stocks_router._stocks_cache.get_stale(
            stocks_router.STOCKS_CACHE_KEY
        )
        assert surviving is not None
        assert surviving["AZN.L"].price == pytest.approx(good["AZN.L"].price)
        assert surviving["AZN.L"].error is None

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_cold_start_total_failure_returns_200_error_flagged_batch(
        self, mock_fetch, client
    ):
        # Nothing has ever succeeded: keep the existing behavior (200 with
        # error-flagged rows, which the frontend greys out) rather than 500.
        mock_fetch.return_value = _all_failed_summaries()

        response = client.get("/api/stocks")

        assert response.status_code == 200
        body = response.json()
        assert len(body["stocks"]) == 20
        for stock in body["stocks"]:
            assert stock["is_stale"] is True
            assert stock["error"] is not None
            assert stock["price"] is None

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_single_ticker_endpoint_also_serves_stale_on_total_failure(
        self, mock_fetch, client
    ):
        good = self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()

        response = client.get("/api/stocks/AZN.L")

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AZN.L"
        assert body["price"] == pytest.approx(good["AZN.L"].price)
        assert body["is_stale"] is True

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_partial_failure_is_still_cached_as_success(self, mock_fetch, client):
        # Only a *total* failure counts as "no fresh data"; a batch with
        # some good rows is a normal successful refresh and must still be
        # cached (and must not trip the backoff).
        summaries = _all_success_summaries()
        summaries["BP.L"] = _summary(
            "BP.L", is_stale=True, error="yfinance error: boom"
        )
        mock_fetch.return_value = summaries

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert stocks_router.STOCKS_CACHE_KEY in stocks_router._stocks_cache._cache
        assert stocks_router._backoff.consecutive_failures == 0


class TestRateLimitBackoff:
    """Plan v3: back off between refresh attempts instead of hammering Yahoo.

    All timing is driven by the injected `fake_clock`; nothing sleeps.
    """

    def _prime_stale(self) -> dict[str, StockSummary]:
        good = _all_success_summaries()
        stocks_router._stocks_cache._stale[stocks_router.STOCKS_CACHE_KEY] = good
        return good

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_repeat_requests_during_backoff_window_do_not_hit_yahoo(
        self, mock_fetch, client, fake_clock
    ):
        self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()

        first = client.get("/api/stocks")
        assert first.status_code == 200
        assert mock_fetch.call_count == 1

        # Poll repeatedly, well inside the backoff window: each request is
        # served from stale with no further yfinance traffic at all.
        for _ in range(3):
            fake_clock.advance(20.0)
            response = client.get("/api/stocks")
            assert response.status_code == 200
            assert all(s["is_stale"] is True for s in response.json()["stocks"])
        assert mock_fetch.call_count == 1

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_retry_allowed_once_backoff_window_expires(
        self, mock_fetch, client, fake_clock
    ):
        self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()

        client.get("/api/stocks")
        assert mock_fetch.call_count == 1

        fake_clock.advance(stocks_router.BACKOFF_BASE_SECONDS + 1)
        client.get("/api/stocks")
        assert mock_fetch.call_count == 2

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_backoff_doubles_per_failure_and_is_capped(
        self, mock_fetch, client, fake_clock, monkeypatch
    ):
        monkeypatch.setattr(stocks_router, "BACKOFF_BASE_SECONDS", 10.0)
        monkeypatch.setattr(stocks_router, "BACKOFF_MAX_SECONDS", 40.0)
        self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()

        observed = []
        for _ in range(4):
            client.get("/api/stocks")
            observed.append(stocks_router._backoff.blocked_until - fake_clock.now)
            # Step past the window so the next request is allowed to retry.
            fake_clock.advance(observed[-1] + 1)

        assert observed == [10.0, 20.0, 40.0, 40.0]
        assert mock_fetch.call_count == 4

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_successful_refresh_resets_backoff(self, mock_fetch, client, fake_clock):
        self._prime_stale()
        mock_fetch.return_value = _all_failed_summaries()
        client.get("/api/stocks")
        assert stocks_router._backoff.consecutive_failures == 1

        fake_clock.advance(stocks_router.BACKOFF_BASE_SECONDS + 1)
        mock_fetch.return_value = _all_success_summaries()
        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert all(s["is_stale"] is False for s in response.json()["stocks"])
        assert stocks_router._backoff.consecutive_failures == 0
        assert stocks_router._backoff.blocked_until == 0.0

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_cache_hit_during_backoff_window_does_not_reset_backoff(
        self, mock_fetch, client, fake_clock
    ):
        # A cached read performs no fetch, so it must not be mistaken for a
        # successful refresh and clear an active backoff -- the TTL (90s) is
        # shorter than the max backoff (600s), so that would defeat it.
        mock_fetch.return_value = _all_success_summaries()
        client.get("/api/stocks")
        stocks_router._backoff.record_failure(_all_failed_summaries())
        failures_before = stocks_router._backoff.consecutive_failures

        client.get("/api/stocks")

        assert stocks_router._backoff.consecutive_failures == failures_before

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_cold_start_backoff_serves_last_failed_batch_without_refetching(
        self, mock_fetch, client, fake_clock
    ):
        # Nothing known-good exists, so the error-flagged batch is all we
        # have -- but we still must not re-hit Yahoo inside the window.
        mock_fetch.return_value = _all_failed_summaries()

        first = client.get("/api/stocks")
        assert first.status_code == 200
        assert mock_fetch.call_count == 1

        fake_clock.advance(1.0)
        second = client.get("/api/stocks")

        assert second.status_code == 200
        assert len(second.json()["stocks"]) == 20
        assert mock_fetch.call_count == 1


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

    @patch("app.routers.stocks.yfinance_client.fetch_summaries")
    def test_actual_get_request_includes_allow_origin_header(self, mock_fetch, client):
        mock_fetch.return_value = _all_success_summaries()

        response = client.get("/api/stocks", headers={"Origin": ALLOWED_ORIGIN})

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
