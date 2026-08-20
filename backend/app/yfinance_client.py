"""All ``yfinance`` calls live here, isolated from the rest of the app.

Two entry points:

- :func:`fetch_summaries` — one ``Ticker.history()`` call per symbol,
  deriving the latest price and the previous close from the returned daily
  bars.
- :func:`fetch_history` — a single ``yf.Ticker(symbol).history(...)`` call,
  with a period/interval fallback when the primary window is empty.

Both functions are per-symbol fault tolerant: any exception, missing data,
or empty frame is caught and converted into an error result (``is_stale``
and/or ``error`` set on the returned schema object). Neither function ever
raises for a per-symbol failure — a bad ticker must not take down a batch.

Prices/previous closes are converted from GBX/GBp (pence, the currency
Yahoo reports for many LSE-listed tickers) to GBP (pounds) by dividing by
100; the reported ``currency`` is normalised to ``"GBP"`` in that case.

HTTP request budget (yfinance 1.6.0)
------------------------------------
``yf.Tickers(...)`` does no batching at the network level — it is a plain
container of ``Ticker`` objects, and every attribute read on one of them
issues its own HTTP request. In particular ``fast_info.last_price``
triggers ``history(period="1y")`` and ``fast_info.previous_close``
triggers a *second* request (``history(period="5d", interval="1h",
prepost=True)``). Reading both for 20 tickers is ~40 requests per refresh,
which sustained IP-level 429s (``YFRateLimitError``).

So this module deliberately uses exactly **one** ``history()`` call per
symbol and derives everything from it:

- ``price``          = last non-NaN daily close
- ``previous_close`` = the daily close before that
- ``currency``       = read from the chart metadata that arrived with the
  *same* response (see :func:`_currency_from_history`)

Note that :attr:`yfinance.Ticker.history_metadata` must **not** be used
here: in 1.6.0 ``get_history_metadata()`` re-requests ``period="5d",
interval="1h"`` whenever ``tradingPeriods`` is absent from the cached
metadata, and it always is for daily bars — that would silently put the
second request per ticker back.
"""

from __future__ import annotations

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from app.schemas import HistoryPoint, HistoryResponse, StockSummary
from app.tickers import TICKERS_BY_SYMBOL

# Currency code Yahoo reports for pence-denominated LSE quotes.
PENCE_CURRENCY = "GBp"
POUNDS_CURRENCY = "GBP"
PENCE_PER_POUND = 100

# Daily bars: enough days to always span a previous close across weekends
# and bank holidays, while still being a single request.
SUMMARY_PERIOD = "5d"
SUMMARY_INTERVAL = "1d"

PRIMARY_HISTORY_PERIOD = "1d"
PRIMARY_HISTORY_INTERVAL = "5m"
FALLBACK_HISTORY_PERIOD = "5d"
FALLBACK_HISTORY_INTERVAL = "15m"

# Error-message prefixes. The rate-limit prefix lets callers (and the UI)
# tell "Yahoo is throttling us" apart from "this ticker is broken".
ERROR_PREFIX = "yfinance error"
RATE_LIMIT_ERROR_PREFIX = "yfinance rate limited"


def _format_error(exc: BaseException) -> str:
    """Render ``exc`` as an error string, flagging rate limiting distinctly."""
    if isinstance(exc, YFRateLimitError):
        return f"{RATE_LIMIT_ERROR_PREFIX}: {exc}"
    return f"{ERROR_PREFIX}: {exc}"


def is_rate_limit_error(message: str | None) -> bool:
    """True if ``message`` was produced from a :class:`YFRateLimitError`."""
    return bool(message) and message.startswith(RATE_LIMIT_ERROR_PREFIX)


def _currency_from_history(ticker) -> str | None:
    """Return the quote currency Yahoo sent alongside the history response.

    ``Ticker.history()`` stores the chart response's ``meta`` block on the
    lazily-created ``PriceHistory`` object, so it is already in memory and
    reading it costs **no** extra HTTP request — unlike the public
    ``Ticker.history_metadata`` property, which re-fetches intraday data to
    populate ``tradingPeriods`` (see this module's docstring).

    Returns ``None`` when the metadata is unavailable or malformed, in
    which case the caller falls back to assuming pounds and applies no
    conversion.
    """
    try:
        price_history = getattr(ticker, "_price_history", None)
        if price_history is None:
            return None
        metadata = getattr(price_history, "_history_metadata", None)
        if not isinstance(metadata, dict):
            return None
        currency = metadata.get("currency")
        return currency if isinstance(currency, str) else None
    except Exception:  # noqa: BLE001 - metadata is best-effort only
        return None


def _error_summary(symbol: str, name: str, sector: str, message: str) -> StockSummary:
    return StockSummary(
        ticker=symbol,
        name=name,
        sector=sector,
        price=None,
        currency=POUNDS_CURRENCY,
        previous_close=None,
        change=None,
        change_percent=None,
        is_stale=True,
        error=message,
    )


def fetch_summaries(symbols: list[str]) -> dict[str, StockSummary]:
    """Fetch live summaries for ``symbols`` using one HTTP request each.

    Returns a dict keyed by each input symbol (every symbol in ``symbols``
    is present in the result, even on failure). Company ``name``/``sector``
    are taken from :data:`app.tickers.TICKERS_BY_SYMBOL`; ``price``,
    ``previous_close``, ``change`` and ``change_percent`` are derived from
    a single ``Ticker.history(period="5d", interval="1d")`` call, with
    GBp->GBP conversion applied when the response's metadata reports the
    quote currency as ``"GBp"``.

    A per-symbol exception, an empty frame, or fewer than two usable daily
    closes results in that symbol's entry being an error result
    (``is_stale=True``, ``error`` set, numeric fields ``None``) rather than
    raising or omitting the symbol.
    """
    results: dict[str, StockSummary] = {}

    for symbol in symbols:
        info = TICKERS_BY_SYMBOL.get(symbol)
        name = info.name if info is not None else symbol
        sector = info.sector if info is not None else ""

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=SUMMARY_PERIOD, interval=SUMMARY_INTERVAL)

            if df is None or df.empty:
                raise ValueError("no price data returned")

            closes = df["Close"].dropna()
            if len(closes) < 2:
                raise ValueError("not enough closes to derive a previous close")

            price = float(closes.iloc[-1])
            previous_close = float(closes.iloc[-2])

            if previous_close == 0:
                raise ValueError("previous_close is zero")

            currency = _currency_from_history(ticker) or POUNDS_CURRENCY

            if currency == PENCE_CURRENCY:
                price = price / PENCE_PER_POUND
                previous_close = previous_close / PENCE_PER_POUND
                currency = POUNDS_CURRENCY

            change = price - previous_close
            change_percent = (change / previous_close) * 100

            results[symbol] = StockSummary(
                ticker=symbol,
                name=name,
                sector=sector,
                price=price,
                currency=currency,
                previous_close=previous_close,
                change=change,
                change_percent=change_percent,
                is_stale=False,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - per-symbol isolation
            results[symbol] = _error_summary(symbol, name, sector, _format_error(exc))

    return results


def fetch_history(symbol: str) -> HistoryResponse:
    """Fetch intraday history for ``symbol``.

    Tries ``period="1d", interval="5m"`` first. If that returns an empty
    frame, falls back to ``period="5d", interval="15m"``. Any failure —
    an exception from ``yfinance``, or both attempts yielding no usable
    data — is caught and returned as an error :class:`HistoryResponse`
    (``is_stale=True``, ``error`` set, ``points=[]``) rather than raised.
    """
    period = PRIMARY_HISTORY_PERIOD
    interval = PRIMARY_HISTORY_INTERVAL

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)

        if df is None or df.empty:
            period = FALLBACK_HISTORY_PERIOD
            interval = FALLBACK_HISTORY_INTERVAL
            df = ticker.history(period=period, interval=interval)

        if df is None or df.empty:
            return HistoryResponse(
                ticker=symbol,
                interval=interval,
                range=period,
                points=[],
                is_stale=True,
                error=f"{ERROR_PREFIX}: no history data available",
            )

        points = [
            HistoryPoint(t=timestamp.isoformat(), close=float(close))
            for timestamp, close in zip(df.index, df["Close"])
        ]

        return HistoryResponse(
            ticker=symbol,
            interval=interval,
            range=period,
            points=points,
            is_stale=False,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - per-symbol isolation
        return HistoryResponse(
            ticker=symbol,
            interval=interval,
            range=period,
            points=[],
            is_stale=True,
            error=_format_error(exc),
        )
