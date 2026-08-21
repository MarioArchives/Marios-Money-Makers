"""Tests for `app.alpaca_client` — the Alpaca Market Data API boundary.

No network: an `httpx.MockTransport` is injected through the module's
`_transport` test seam, so the full request/response path (URL, params,
headers, pagination, error handling) is exercised against canned payloads
shaped exactly like real Alpaca responses.

Credentials/base-url/feed are monkeypatched on `app.config` (which the
client reads at call time).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app import alpaca_client, config
from app.alpaca_client import AlpacaError, Bar
from app.tickers import TICKERS_BY_SYMBOL

START = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch):
    monkeypatch.setattr(config, "ALPACA_KEY_ID", "test-key-id")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "ALPACA_DATA_BASE_URL", "https://data.alpaca.test")
    monkeypatch.setattr(config, "ALPACA_FEED", "iex")
    yield


def _install(monkeypatch, handler) -> list[httpx.Request]:
    """Install a MockTransport that records every request it serves."""
    seen: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        alpaca_client, "_transport", httpx.MockTransport(recording_handler)
    )
    return seen


def _raw_bar(ts: str, close: float) -> dict:
    return {
        "t": ts,
        "o": close - 0.5,
        "h": close + 0.5,
        "l": close - 1.0,
        "c": close,
        "v": 1000,
        "vw": close - 0.1,
        "n": 42,
    }


def _snapshot(
    price: float,
    prev_close: float | None,
    *,
    minute_ts: str = "2026-08-19T19:59:00Z",
    include_latest_trade: bool = True,
) -> dict:
    snap: dict = {
        "dailyBar": _raw_bar("2026-08-19T04:00:00Z", price),
        "minuteBar": _raw_bar(minute_ts, price - 0.05),
    }
    if include_latest_trade:
        snap["latestTrade"] = {"p": price, "t": "2026-08-19T19:59:59.44Z"}
    if prev_close is not None:
        snap["prevDailyBar"] = _raw_bar("2026-08-18T04:00:00Z", prev_close)
    return snap


class TestNormalizeTimestamp:
    def test_plain_z_timestamp_unchanged(self):
        assert (
            alpaca_client.normalize_timestamp("2026-08-19T13:30:00Z")
            == "2026-08-19T13:30:00Z"
        )

    def test_fractional_seconds_stripped(self):
        assert (
            alpaca_client.normalize_timestamp("2026-08-19T13:30:00.123456789Z")
            == "2026-08-19T13:30:00Z"
        )

    def test_explicit_utc_offset_becomes_z(self):
        assert (
            alpaca_client.normalize_timestamp("2026-08-19T13:30:00+00:00")
            == "2026-08-19T13:30:00Z"
        )

    def test_normalized_strings_sort_chronologically(self):
        raw = [
            "2026-08-19T13:30:05.9Z",
            "2026-08-19T13:30:00+00:00",
            "2026-12-01T00:00:00Z",
            "2026-08-19T13:31:00.000001Z",
        ]
        normalized = [alpaca_client.normalize_timestamp(ts) for ts in raw]
        assert sorted(normalized) == [
            "2026-08-19T13:30:00Z",
            "2026-08-19T13:30:05Z",
            "2026-08-19T13:31:00Z",
            "2026-12-01T00:00:00Z",
        ]


class TestIsRateLimitError:
    def test_true_for_rate_limit_prefixed_message(self):
        assert alpaca_client.is_rate_limit_error(
            f"{alpaca_client.RATE_LIMIT_ERROR_PREFIX}: too many requests"
        )

    def test_false_for_plain_error_message(self):
        assert not alpaca_client.is_rate_limit_error(
            f"{alpaca_client.ERROR_PREFIX}: connection refused"
        )

    def test_false_for_none(self):
        assert not alpaca_client.is_rate_limit_error(None)


class TestFetchSummaries:
    def test_parses_prices_and_derives_change_with_one_request(self, monkeypatch):
        payload = {
            "AAPL": _snapshot(316.9, 310.16),
            "MSFT": _snapshot(484.42, 481.82),
        }
        seen = _install(
            monkeypatch, lambda request: httpx.Response(200, json=payload)
        )

        summaries, minute_bars = alpaca_client.fetch_summaries(["AAPL", "MSFT"])

        assert len(seen) == 1, "one snapshots request must cover the whole batch"
        request = seen[0]
        assert request.url.path == "/v2/stocks/snapshots"
        assert set(request.url.params["symbols"].split(",")) == {"AAPL", "MSFT"}
        assert request.url.params["feed"] == "iex"
        assert request.headers["APCA-API-KEY-ID"] == "test-key-id"
        assert request.headers["APCA-API-SECRET-KEY"] == "test-secret"

        aapl = summaries["AAPL"]
        assert aapl.price == pytest.approx(316.9)
        assert aapl.previous_close == pytest.approx(310.16)
        assert aapl.change == pytest.approx(316.9 - 310.16)
        assert aapl.change_percent == pytest.approx((316.9 - 310.16) / 310.16 * 100)
        assert aapl.currency == "USD"
        assert aapl.name == TICKERS_BY_SYMBOL["AAPL"].name
        assert aapl.sector == TICKERS_BY_SYMBOL["AAPL"].sector
        assert aapl.is_stale is False
        assert aapl.error is None
        assert summaries["MSFT"].price == pytest.approx(484.42)
        assert set(minute_bars) == {"AAPL", "MSFT"}

    def test_minute_bars_are_persistable_bars(self, monkeypatch):
        payload = {"AAPL": _snapshot(316.9, 310.16, minute_ts="2026-08-19T19:59:00Z")}
        _install(monkeypatch, lambda request: httpx.Response(200, json=payload))

        _, minute_bars = alpaca_client.fetch_summaries(["AAPL"])

        bar = minute_bars["AAPL"]
        assert isinstance(bar, Bar)
        assert bar.ts == "2026-08-19T19:59:00Z"
        assert bar.price == pytest.approx(316.85)  # minuteBar close
        analytics = json.loads(bar.analytics)
        assert set(analytics) == {"o", "h", "l", "c", "v", "vw", "n"}
        assert analytics["c"] == pytest.approx(316.85)

    def test_symbol_missing_from_response_is_error_entry_others_ok(
        self, monkeypatch
    ):
        payload = {"AAPL": _snapshot(316.9, 310.16)}  # MSFT absent
        _install(monkeypatch, lambda request: httpx.Response(200, json=payload))

        summaries, minute_bars = alpaca_client.fetch_summaries(["AAPL", "MSFT"])

        assert summaries["AAPL"].error is None
        msft = summaries["MSFT"]
        assert msft.is_stale is True
        assert msft.error is not None
        assert msft.error.startswith(alpaca_client.ERROR_PREFIX)
        assert msft.price is None
        # Name/sector still come from the local universe, not the API.
        assert msft.name == TICKERS_BY_SYMBOL["MSFT"].name
        assert "MSFT" not in minute_bars

    def test_missing_prev_daily_bar_keeps_price_without_change(self, monkeypatch):
        # Thin IEX data (e.g. BRK.B): no previous close is a degraded
        # success, not a failure -- the leaderboard can still show the price.
        payload = {"BRK.B": _snapshot(499.82, None)}
        _install(monkeypatch, lambda request: httpx.Response(200, json=payload))

        summaries, _ = alpaca_client.fetch_summaries(["BRK.B"])

        brk = summaries["BRK.B"]
        assert brk.price == pytest.approx(499.82)
        assert brk.previous_close is None
        assert brk.change is None
        assert brk.change_percent is None
        assert brk.is_stale is False
        assert brk.error is None

    def test_missing_latest_trade_falls_back_to_minute_then_daily_bar(
        self, monkeypatch
    ):
        no_trade = _snapshot(316.9, 310.16, include_latest_trade=False)
        daily_only = {
            "dailyBar": _raw_bar("2026-08-19T04:00:00Z", 200.0),
            "prevDailyBar": _raw_bar("2026-08-18T04:00:00Z", 198.0),
        }
        payload = {"AAPL": no_trade, "MSFT": daily_only}
        _install(monkeypatch, lambda request: httpx.Response(200, json=payload))

        summaries, _ = alpaca_client.fetch_summaries(["AAPL", "MSFT"])

        # AAPL: latestTrade absent -> minuteBar close.
        assert summaries["AAPL"].price == pytest.approx(316.85)
        # MSFT: latestTrade and minuteBar absent -> dailyBar close.
        assert summaries["MSFT"].price == pytest.approx(200.0)

    def test_429_produces_all_error_batch_flagged_rate_limited(self, monkeypatch):
        _install(
            monkeypatch,
            lambda request: httpx.Response(429, json={"message": "too many requests"}),
        )

        summaries, minute_bars = alpaca_client.fetch_summaries(["AAPL", "MSFT"])

        assert set(summaries) == {"AAPL", "MSFT"}
        for entry in summaries.values():
            assert entry.is_stale is True
            assert entry.price is None
            assert alpaca_client.is_rate_limit_error(entry.error)
        assert minute_bars == {}

    def test_network_error_produces_all_error_batch_not_rate_limited(
        self, monkeypatch
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _install(monkeypatch, handler)

        summaries, minute_bars = alpaca_client.fetch_summaries(["AAPL"])

        entry = summaries["AAPL"]
        assert entry.is_stale is True
        assert entry.error is not None
        assert entry.error.startswith(alpaca_client.ERROR_PREFIX)
        assert not alpaca_client.is_rate_limit_error(entry.error)
        assert minute_bars == {}


class TestFetchBars:
    def test_request_shape(self, monkeypatch):
        seen = _install(
            monkeypatch,
            lambda request: httpx.Response(
                200, json={"bars": [], "next_page_token": None}
            ),
        )

        alpaca_client.fetch_bars("AAPL", alpaca_client.TIMEFRAME_MINUTE, START)

        assert len(seen) == 1
        request = seen[0]
        assert request.url.path == "/v2/stocks/AAPL/bars"
        params = request.url.params
        assert params["timeframe"] == "1Min"
        assert params["feed"] == "iex"
        # `start` is RFC3339 for the given datetime...
        assert params["start"].startswith("2026-08-19T12:00:00")
        # ...and `end` must be omitted so Alpaca defaults to "now".
        assert "end" not in params
        assert request.headers["APCA-API-KEY-ID"] == "test-key-id"

    def test_returns_normalized_bars(self, monkeypatch):
        payload = {
            "bars": [
                _raw_bar("2026-08-19T13:30:00.123456789Z", 310.85),
                _raw_bar("2026-08-19T13:31:00Z", 310.61),
            ],
            "next_page_token": None,
        }
        _install(monkeypatch, lambda request: httpx.Response(200, json=payload))

        bars = alpaca_client.fetch_bars(
            "AAPL", alpaca_client.TIMEFRAME_MINUTE, START
        )

        assert [b.ts for b in bars] == [
            "2026-08-19T13:30:00Z",  # fractional seconds normalized away
            "2026-08-19T13:31:00Z",
        ]
        assert [b.price for b in bars] == pytest.approx([310.85, 310.61])
        assert json.loads(bars[0].analytics)["v"] == 1000

    def test_follows_pagination(self, monkeypatch):
        page_one = {
            "bars": [_raw_bar("2026-08-19T13:30:00Z", 310.85)],
            "next_page_token": "tok-1",
        }
        page_two = {
            "bars": [_raw_bar("2026-08-19T13:31:00Z", 310.61)],
            "next_page_token": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page_token") == "tok-1":
                return httpx.Response(200, json=page_two)
            return httpx.Response(200, json=page_one)

        seen = _install(monkeypatch, handler)

        bars = alpaca_client.fetch_bars(
            "AAPL", alpaca_client.TIMEFRAME_MINUTE, START
        )

        assert len(seen) == 2
        assert "page_token" not in seen[0].url.params
        assert seen[1].url.params["page_token"] == "tok-1"
        assert [b.ts for b in bars] == [
            "2026-08-19T13:30:00Z",
            "2026-08-19T13:31:00Z",
        ]

    def test_empty_response_returns_empty_list_not_error(self, monkeypatch):
        # Weekend / market closed: a 200 with no bars is a legitimate
        # empty result, never an exception.
        _install(
            monkeypatch,
            lambda request: httpx.Response(
                200, json={"bars": [], "next_page_token": None}
            ),
        )

        assert (
            alpaca_client.fetch_bars("AAPL", alpaca_client.TIMEFRAME_MINUTE, START)
            == []
        )

    def test_429_raises_rate_limited_alpaca_error(self, monkeypatch):
        _install(monkeypatch, lambda request: httpx.Response(429, json={}))

        with pytest.raises(AlpacaError) as exc_info:
            alpaca_client.fetch_bars("AAPL", alpaca_client.TIMEFRAME_MINUTE, START)

        assert exc_info.value.is_rate_limit is True
        assert alpaca_client.is_rate_limit_error(str(exc_info.value))

    def test_server_error_raises_plain_alpaca_error(self, monkeypatch):
        _install(monkeypatch, lambda request: httpx.Response(500, json={}))

        with pytest.raises(AlpacaError) as exc_info:
            alpaca_client.fetch_bars("AAPL", alpaca_client.TIMEFRAME_MINUTE, START)

        assert exc_info.value.is_rate_limit is False
        assert str(exc_info.value).startswith(alpaca_client.ERROR_PREFIX)

    def test_network_error_raises_alpaca_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _install(monkeypatch, handler)

        with pytest.raises(AlpacaError):
            alpaca_client.fetch_bars("AAPL", alpaca_client.TIMEFRAME_MINUTE, START)
