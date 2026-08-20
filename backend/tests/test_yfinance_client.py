"""Tests for app.yfinance_client.

All yfinance calls are mocked via unittest.mock.patch on the ``yf`` module
as imported inside app.yfinance_client — no real network calls are made.

NOTE (plan v3): these tests were originally written against the
``yf.Tickers(...)`` + ``fast_info`` call shape. That shape cost two HTTP
requests per ticker (fast_info.last_price -> history(period="1y"),
fast_info.previous_close -> a second history call), i.e. ~40 requests per
20s refresh, which triggered sustained IP-level 429s from Yahoo. The
client now issues exactly one ``Ticker.history(period="5d", interval="1d")``
call per symbol and derives price/previous close from the returned bars.
Only the mocked *call shape* changed here — every behavioral guarantee
(GBp->GBP conversion, per-ticker error isolation, missing-field handling,
history 1d/5m -> 5d/15m fallback) is still asserted below.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.schemas import HistoryResponse, StockSummary
from app.yfinance_client import fetch_history, fetch_summaries


def _make_daily_df(closes: list[float | None], start_day: int = 15) -> pd.DataFrame:
    """Build a daily-bars frame shaped like ``Ticker.history(interval='1d')``."""
    timestamps = [
        datetime(2026, 8, start_day + i, tzinfo=timezone.utc)
        for i in range(len(closes))
    ]
    values = [float("nan") if c is None else c for c in closes]
    return pd.DataFrame({"Close": values}, index=pd.DatetimeIndex(timestamps))


def _make_summary_ticker_mock(df=None, history_side_effect=None, currency=None):
    """Build a MagicMock standing in for a single ``yf.Ticker``.

    ``currency`` seeds the chart metadata that real yfinance caches on the
    ``PriceHistory`` object during ``history()`` — the client reads the
    quote currency from there precisely because it costs no extra HTTP
    request (``Ticker.history_metadata`` would re-fetch intraday data).
    Left as ``None``, the mock exposes no usable metadata and the client
    must fall back to assuming pounds.
    """
    ticker_mock = MagicMock()
    if history_side_effect is not None:
        ticker_mock.history.side_effect = history_side_effect
    else:
        ticker_mock.history.return_value = df
    if currency is not None:
        ticker_mock._price_history._history_metadata = {"currency": currency}
    else:
        ticker_mock._price_history = None
    return ticker_mock


def _make_history_df(timestamps: list[datetime], closes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex(timestamps)
    return pd.DataFrame({"Close": closes}, index=index)


def _empty_history_df() -> pd.DataFrame:
    return pd.DataFrame({"Close": pd.Series(dtype="float64")}, index=pd.DatetimeIndex([]))


class TestFetchSummaries:
    @patch("app.yfinance_client.yf.Ticker")
    def test_success_computes_price_change_and_percent(self, mock_ticker_cls):
        # Last close is the live-ish price; the one before it is the
        # previous close the change is measured against.
        ticker_mock = _make_summary_ticker_mock(
            df=_make_daily_df([108.0, 110.00, 112.34]), currency="GBP"
        )
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["AZN.L"])

        assert set(result.keys()) == {"AZN.L"}
        summary = result["AZN.L"]
        assert isinstance(summary, StockSummary)
        assert summary.ticker == "AZN.L"
        assert summary.name == "AstraZeneca"
        assert summary.sector == "Pharmaceuticals"
        assert summary.price == pytest.approx(112.34)
        assert summary.previous_close == pytest.approx(110.00)
        assert summary.change == pytest.approx(2.34)
        assert summary.change_percent == pytest.approx((2.34 / 110.00) * 100)
        assert summary.currency == "GBP"
        assert summary.is_stale is False
        assert summary.error is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_gbp_pence_conversion_divides_by_100(self, mock_ticker_cls):
        # Many LSE quotes come back from yfinance in pence (GBp);
        # 11234.0 GBp must become 112.34 GBP.
        ticker_mock = _make_summary_ticker_mock(
            df=_make_daily_df([10800.0, 11000.0, 11234.0]), currency="GBp"
        )
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["VOD.L"])

        summary = result["VOD.L"]
        assert summary.currency == "GBP"
        assert summary.price == pytest.approx(112.34)
        assert summary.previous_close == pytest.approx(110.00)
        assert summary.change == pytest.approx(2.34)
        assert summary.change_percent == pytest.approx((2.34 / 110.00) * 100)
        assert summary.is_stale is False
        assert summary.error is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_exception_from_yfinance_returns_error_result_not_raised(
        self, mock_ticker_cls
    ):
        ticker_mock = _make_summary_ticker_mock(
            history_side_effect=RuntimeError("network down")
        )
        mock_ticker_cls.return_value = ticker_mock

        # Must not raise.
        result = fetch_summaries(["BP.L"])

        assert set(result.keys()) == {"BP.L"}
        summary = result["BP.L"]
        assert isinstance(summary, StockSummary)
        assert summary.is_stale is True
        assert summary.error is not None
        assert summary.price is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_rate_limit_error_is_flagged_distinctly_not_raised(self, mock_ticker_cls):
        from yfinance.exceptions import YFRateLimitError

        from app.yfinance_client import is_rate_limit_error

        ticker_mock = _make_summary_ticker_mock(
            history_side_effect=YFRateLimitError()
        )
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["BP.L"])

        summary = result["BP.L"]
        assert summary.is_stale is True
        assert summary.price is None
        assert is_rate_limit_error(summary.error) is True
        # A plain failure must not be mistaken for rate limiting.
        assert is_rate_limit_error("yfinance error: network down") is False

    @patch("app.yfinance_client.yf.Ticker")
    def test_missing_latest_close_is_error_not_crash(self, mock_ticker_cls):
        # Replaces the old fast_info(last_price=None) case: a frame whose
        # only usable close is NaN cannot yield a price.
        ticker_mock = _make_summary_ticker_mock(
            df=_make_daily_df([None, None]), currency="GBP"
        )
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["BARC.L"])

        summary = result["BARC.L"]
        assert summary.is_stale is True
        assert summary.error is not None
        assert summary.price is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_missing_previous_close_is_error_not_crash(self, mock_ticker_cls):
        # Replaces the old fast_info(previous_close=None) case: a single
        # bar gives a price but nothing to compare it against.
        ticker_mock = _make_summary_ticker_mock(
            df=_make_daily_df([100.0]), currency="GBP"
        )
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["LLOY.L"])

        summary = result["LLOY.L"]
        assert summary.is_stale is True
        assert summary.error is not None
        assert summary.price is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_empty_frame_is_error_not_crash(self, mock_ticker_cls):
        ticker_mock = _make_summary_ticker_mock(df=_empty_history_df())
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["TSCO.L"])

        summary = result["TSCO.L"]
        assert summary.is_stale is True
        assert summary.error is not None
        assert summary.price is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_zero_previous_close_is_error_not_division_by_zero(self, mock_ticker_cls):
        ticker_mock = _make_summary_ticker_mock(
            df=_make_daily_df([0.0, 100.0]), currency="GBP"
        )
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["NG.L"])

        summary = result["NG.L"]
        assert summary.is_stale is True
        assert summary.error is not None

    @patch("app.yfinance_client.yf.Ticker")
    def test_multiple_symbols_fetched_independently(self, mock_ticker_cls):
        # Replaces test_one_batched_call_covers_multiple_symbols_independently.
        # yf.Tickers() batching was cosmetic (zero network at construction),
        # so the client now creates one Ticker per symbol; the behavioral
        # guarantee under test is unchanged -- one symbol's failure must not
        # affect another's result.
        good = _make_summary_ticker_mock(
            df=_make_daily_df([95.0, 100.0]), currency="GBP"
        )
        bad = _make_summary_ticker_mock(history_side_effect=RuntimeError("boom"))
        mock_ticker_cls.side_effect = lambda symbol: {"AZN.L": good, "BP.L": bad}[symbol]

        result = fetch_summaries(["AZN.L", "BP.L"])

        assert mock_ticker_cls.call_count == 2
        assert result["AZN.L"].error is None
        assert result["AZN.L"].is_stale is False
        assert result["AZN.L"].price == pytest.approx(100.0)
        assert result["BP.L"].error is not None
        assert result["BP.L"].is_stale is True

    @patch("app.yfinance_client.yf.Ticker")
    def test_exactly_one_http_shaped_call_per_symbol(self, mock_ticker_cls):
        """The core rate-limit fix: one request per ticker, not two.

        Guards against regressing to ``fast_info``, whose ``last_price``
        and ``previous_close`` each issue their own HTTP request.
        """
        tickers = {
            symbol: _make_summary_ticker_mock(
                df=_make_daily_df([95.0, 100.0]), currency="GBP"
            )
            for symbol in ("AZN.L", "BP.L", "GSK.L")
        }
        mock_ticker_cls.side_effect = lambda symbol: tickers[symbol]

        result = fetch_summaries(["AZN.L", "BP.L", "GSK.L"])

        assert len(result) == 3
        for symbol, ticker_mock in tickers.items():
            assert ticker_mock.history.call_count == 1, symbol
            ticker_mock.history.assert_called_once_with(period="5d", interval="1d")

    @patch("app.yfinance_client.yf.Ticker")
    def test_does_not_touch_fast_info_or_history_metadata(self, mock_ticker_cls):
        """Neither of the extra-request-triggering accessors may be read.

        ``fast_info`` costs two requests; ``Ticker.history_metadata`` in
        yfinance 1.6.0 re-fetches 5d/1h intraday data whenever
        ``tradingPeriods`` is missing (always, for daily bars).
        """
        ticker_mock = _make_summary_ticker_mock(
            df=_make_daily_df([95.0, 100.0]), currency="GBP"
        )

        def _forbidden(*_args, **_kwargs):
            raise AssertionError("extra-HTTP-request accessor used")

        type(ticker_mock).fast_info = property(_forbidden)
        type(ticker_mock).history_metadata = property(_forbidden)
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["AZN.L"])

        assert result["AZN.L"].error is None
        assert result["AZN.L"].price == pytest.approx(100.0)

    @patch("app.yfinance_client.yf.Ticker")
    def test_unavailable_currency_metadata_defaults_to_pounds_without_conversion(
        self, mock_ticker_cls
    ):
        ticker_mock = _make_summary_ticker_mock(df=_make_daily_df([95.0, 100.0]))
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_summaries(["AZN.L"])

        summary = result["AZN.L"]
        assert summary.currency == "GBP"
        assert summary.price == pytest.approx(100.0)
        assert summary.error is None


class TestFetchHistory:
    @patch("app.yfinance_client.yf.Ticker")
    def test_default_1d_5m_maps_dataframe_to_points(self, mock_ticker_cls):
        timestamps = [
            datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 19, 9, 5, tzinfo=timezone.utc),
            datetime(2026, 8, 19, 9, 10, tzinfo=timezone.utc),
        ]
        df = _make_history_df(timestamps, [100.0, 101.5, 99.75])
        ticker_mock = MagicMock()
        ticker_mock.history.return_value = df
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_history("AZN.L")

        assert isinstance(result, HistoryResponse)
        ticker_mock.history.assert_called_once_with(period="1d", interval="5m")
        assert result.ticker == "AZN.L"
        assert result.interval == "5m"
        assert result.range == "1d"
        assert len(result.points) == 3
        assert [p.close for p in result.points] == pytest.approx([100.0, 101.5, 99.75])
        assert result.points[0].t.startswith("2026-08-19T09:00")
        assert result.is_stale is False
        assert result.error is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_empty_1d_5m_falls_back_to_5d_15m(self, mock_ticker_cls):
        empty_df = _empty_history_df()
        fallback_timestamps = [
            datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        ]
        fallback_df = _make_history_df(fallback_timestamps, [98.0, 99.0])

        ticker_mock = MagicMock()
        ticker_mock.history.side_effect = [empty_df, fallback_df]
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_history("GSK.L")

        assert ticker_mock.history.call_count == 2
        ticker_mock.history.assert_any_call(period="1d", interval="5m")
        ticker_mock.history.assert_any_call(period="5d", interval="15m")
        assert result.ticker == "GSK.L"
        assert result.interval == "15m"
        assert result.range == "5d"
        assert len(result.points) == 2
        assert [p.close for p in result.points] == pytest.approx([98.0, 99.0])
        assert result.is_stale is False
        assert result.error is None

    @patch("app.yfinance_client.yf.Ticker")
    def test_both_empty_returns_error_with_empty_points(self, mock_ticker_cls):
        ticker_mock = MagicMock()
        ticker_mock.history.side_effect = [_empty_history_df(), _empty_history_df()]
        mock_ticker_cls.return_value = ticker_mock

        result = fetch_history("HSBA.L")

        assert ticker_mock.history.call_count == 2
        assert result.ticker == "HSBA.L"
        assert result.points == []
        assert result.is_stale is True
        assert result.error is not None

    @patch("app.yfinance_client.yf.Ticker")
    def test_total_failure_returns_error_with_empty_points_not_raised(
        self, mock_ticker_cls
    ):
        ticker_mock = MagicMock()
        ticker_mock.history.side_effect = RuntimeError("network down")
        mock_ticker_cls.return_value = ticker_mock

        # Must not raise.
        result = fetch_history("RIO.L")

        assert isinstance(result, HistoryResponse)
        assert result.ticker == "RIO.L"
        assert result.points == []
        assert result.is_stale is True
        assert result.error is not None
