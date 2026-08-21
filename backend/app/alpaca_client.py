"""All Alpaca Market Data API calls live here, isolated from the rest of the app.

Two entry points:

- :func:`fetch_summaries` — a single ``GET /v2/stocks/snapshots`` request
  covering every symbol in the batch. Returns both the per-symbol
  :class:`app.schemas.StockSummary` dict *and* each symbol's latest minute
  bar so the caller can persist it to the SQLite backup store.
- :func:`fetch_bars` — ``GET /v2/stocks/{symbol}/bars`` for one symbol at
  one timeframe (``1Min``/``1Hour``/``1Day``), following
  ``next_page_token`` pagination until exhausted.

Fault-tolerance contract:

- ``fetch_summaries`` NEVER raises. A request-level failure (network
  error, 429, any non-2xx) produces an all-error batch — every requested
  symbol present, ``is_stale=True``, ``error`` set — with rate limiting
  flagged distinctly via :data:`RATE_LIMIT_ERROR_PREFIX` so the router's
  backoff can tell "Alpaca is throttling us" apart from "this symbol is
  broken". A symbol merely missing from an otherwise-good response gets a
  per-symbol error entry without affecting the rest of the batch.
- ``fetch_bars`` DOES raise (:class:`AlpacaError`) on request-level
  failure — its caller decides whether SQLite can cover for the outage.
  A legitimately empty bar list (weekend, market closed, thin IEX data)
  is NOT an error: it returns ``[]``.

Requests carry the ``APCA-API-KEY-ID`` / ``APCA-API-SECRET-KEY`` headers
(from ``app.config.ALPACA_KEY_ID`` / ``ALPACA_SECRET_KEY``, read at call
time) and ``feed=iex`` (free plan). Bars requests send ``start`` but no
``end`` (Alpaca defaults to "now", which the IEX feed may serve without
the paid plan's 15-minute SIP restriction).

Timestamps are normalized to exactly ``YYYY-MM-DDTHH:MM:SSZ`` (UTC) so
that lexicographic string comparison in SQLite equals chronological order.
"""

from __future__ import annotations

import json  # noqa: F401 - used by the implementation for Bar.analytics
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.schemas import StockSummary

# Currency Alpaca quotes US equities in.
USD = "USD"

# Error-message prefixes. The rate-limit prefix lets callers (and the UI)
# tell "Alpaca is throttling us" apart from "this symbol is broken".
ERROR_PREFIX = "alpaca error"
RATE_LIMIT_ERROR_PREFIX = "alpaca rate limited"

# Alpaca timeframe strings for the three storage tiers.
TIMEFRAME_MINUTE = "1Min"
TIMEFRAME_HOUR = "1Hour"
TIMEFRAME_MONTH = "1Day"

# Test seam: when set (an ``httpx.BaseTransport``, e.g. ``httpx.MockTransport``),
# `_client()` builds its ``httpx.Client`` on top of it so tests exercise the
# full request/response path without the network.
_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class Bar:
    """One price bar, ready for the SQLite store.

    - ``ts``: bar time, normalized ISO-8601 UTC (``YYYY-MM-DDTHH:MM:SSZ``)
    - ``price``: the bar's close (``c``)
    - ``analytics``: ``json.dumps`` of the raw Alpaca bar fields
      ``{"o", "h", "l", "c", "v", "vw", "n"}``
    """

    ts: str
    price: float
    analytics: str


class AlpacaError(Exception):
    """Request-level Alpaca failure. ``str()`` carries a prefixed message."""

    def __init__(self, message: str, *, is_rate_limit: bool = False) -> None:
        super().__init__(message)
        self.is_rate_limit = is_rate_limit


def is_rate_limit_error(message: str | None) -> bool:
    """True if ``message`` was produced from an HTTP 429 response."""
    return isinstance(message, str) and message.startswith(RATE_LIMIT_ERROR_PREFIX)


def normalize_timestamp(raw: str) -> str:
    """Normalize an Alpaca RFC3339 timestamp to ``YYYY-MM-DDTHH:MM:SSZ``.

    Handles fractional seconds (``2026-08-19T13:30:00.123456789Z``) and
    explicit UTC offsets (``2026-08-19T13:30:00+00:00``); output always
    uses the ``Z`` suffix so string ordering equals chronological ordering.
    """
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    dt = datetime.fromisoformat(normalized)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _client() -> httpx.Client:
    """Build an ``httpx.Client`` for the Alpaca data API.

    Base URL, credentials and feed are read from ``app.config`` at call
    time (monkeypatch-friendly); ``_transport`` is injected when set.
    """
    from app import config

    headers = {
        "APCA-API-KEY-ID": config.ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
    }
    kwargs: dict = {"base_url": config.ALPACA_DATA_BASE_URL, "headers": headers}
    if _transport is not None:
        kwargs["transport"] = _transport
    return httpx.Client(**kwargs)


def fetch_summaries(
    symbols: list[str],
) -> tuple[dict[str, StockSummary], dict[str, Bar]]:
    """Fetch live summaries for ``symbols`` with ONE snapshots request.

    Returns ``(summaries, minute_bars)``:

    - ``summaries``: dict keyed by each input symbol (every symbol present,
      even on failure). Per symbol: ``price`` from ``latestTrade.p``
      (falling back to ``minuteBar.c`` then ``dailyBar.c``),
      ``previous_close`` from ``prevDailyBar.c``, ``change``/
      ``change_percent`` derived, ``currency="USD"``, name/sector from
      :data:`app.tickers.TICKERS_BY_SYMBOL`. A missing ``prevDailyBar``
      (thin IEX data) keeps the price and leaves ``previous_close``/
      ``change``/``change_percent`` as ``None`` — NOT an error entry.
    - ``minute_bars``: each successfully parsed symbol's ``minuteBar`` as a
      :class:`Bar`, for persistence into the ``bars_minute`` table. Empty
      on request-level failure; symbols without a minute bar are absent.

    Never raises; see the module docstring's fault-tolerance contract.
    """
    from app import config
    from app.tickers import TICKERS_BY_SYMBOL

    def _ticker_info(symbol: str) -> tuple[str, str]:
        info = TICKERS_BY_SYMBOL.get(symbol)
        return (info.name, info.sector) if info is not None else ("", "")

    def _error_batch(message: str) -> tuple[dict[str, StockSummary], dict[str, Bar]]:
        summaries: dict[str, StockSummary] = {}
        for symbol in symbols:
            name, sector = _ticker_info(symbol)
            summaries[symbol] = StockSummary(
                ticker=symbol,
                name=name,
                sector=sector,
                price=None,
                currency=USD,
                previous_close=None,
                change=None,
                change_percent=None,
                is_stale=True,
                error=message,
            )
        return summaries, {}

    try:
        with _client() as client:
            response = client.get(
                "/v2/stocks/snapshots",
                params={"symbols": ",".join(symbols), "feed": config.ALPACA_FEED},
            )
    except httpx.HTTPError as exc:
        return _error_batch(f"{ERROR_PREFIX}: {exc}")

    if response.status_code == 429:
        return _error_batch(f"{RATE_LIMIT_ERROR_PREFIX}: {response.text}")
    if response.status_code >= 400:
        return _error_batch(f"{ERROR_PREFIX}: HTTP {response.status_code}")

    body = response.json()

    summaries = {}
    minute_bars: dict[str, Bar] = {}
    for symbol in symbols:
        name, sector = _ticker_info(symbol)
        snap = body.get(symbol)
        if snap is None:
            summaries[symbol] = StockSummary(
                ticker=symbol,
                name=name,
                sector=sector,
                price=None,
                currency=USD,
                previous_close=None,
                change=None,
                change_percent=None,
                is_stale=True,
                error=f"{ERROR_PREFIX}: symbol missing from snapshot response",
            )
            continue

        latest_trade = snap.get("latestTrade")
        minute_bar = snap.get("minuteBar")
        daily_bar = snap.get("dailyBar")
        prev_daily_bar = snap.get("prevDailyBar")

        price: float | None = None
        if latest_trade is not None:
            price = latest_trade.get("p")
        if price is None and minute_bar is not None:
            price = minute_bar.get("c")
        if price is None and daily_bar is not None:
            price = daily_bar.get("c")

        previous_close: float | None = None
        if prev_daily_bar is not None:
            previous_close = prev_daily_bar.get("c")

        change: float | None = None
        change_percent: float | None = None
        if price is not None and previous_close is not None:
            change = price - previous_close
            change_percent = change / previous_close * 100

        summaries[symbol] = StockSummary(
            ticker=symbol,
            name=name,
            sector=sector,
            price=price,
            currency=USD,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            is_stale=False,
            error=None,
        )

        if minute_bar is not None:
            minute_bars[symbol] = Bar(
                ts=normalize_timestamp(minute_bar["t"]),
                price=minute_bar["c"],
                analytics=json.dumps(
                    {k: minute_bar[k] for k in ("o", "h", "l", "c", "v", "vw", "n")}
                ),
            )

    return summaries, minute_bars


def fetch_bars(symbol: str, timeframe: str, start: datetime) -> list[Bar]:
    """Fetch all bars for ``symbol`` at ``timeframe`` from ``start`` to now.

    Follows ``next_page_token`` pagination, concatenating pages in order.
    Returns ``[]`` for a legitimately empty response (not an error).
    Raises :class:`AlpacaError` on any request-level failure (429 flagged
    with ``is_rate_limit=True`` and the rate-limit message prefix).
    """
    from app import config

    bars: list[Bar] = []
    page_token: str | None = None

    with _client() as client:
        while True:
            params = {
                "timeframe": timeframe,
                "feed": config.ALPACA_FEED,
                "start": start.isoformat().replace("+00:00", "Z"),
                "limit": 1000,
            }
            if page_token is not None:
                params["page_token"] = page_token

            try:
                response = client.get(f"/v2/stocks/{symbol}/bars", params=params)
            except httpx.HTTPError as exc:
                raise AlpacaError(f"{ERROR_PREFIX}: {exc}") from exc

            if response.status_code == 429:
                raise AlpacaError(
                    f"{RATE_LIMIT_ERROR_PREFIX}: {response.text}",
                    is_rate_limit=True,
                )
            if response.status_code >= 400:
                raise AlpacaError(f"{ERROR_PREFIX}: HTTP {response.status_code}")

            body = response.json()
            for raw_bar in body.get("bars") or []:
                bars.append(
                    Bar(
                        ts=normalize_timestamp(raw_bar["t"]),
                        price=raw_bar["c"],
                        analytics=json.dumps(
                            {
                                k: raw_bar[k]
                                for k in ("o", "h", "l", "c", "v", "vw", "n")
                            }
                        ),
                    )
                )

            page_token = body.get("next_page_token")
            if not page_token:
                break

    return bars
