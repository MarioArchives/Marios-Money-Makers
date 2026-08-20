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
    """
    stocks_router._stocks_cache = TTLCacheWithLock(ttl=CACHE_TTL_SECONDS)
    stocks_router._history_cache = TTLCacheWithLock(ttl=HISTORY_CACHE_TTL_SECONDS)
    yield


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
