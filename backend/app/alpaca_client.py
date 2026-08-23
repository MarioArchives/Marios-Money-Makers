"""All Alpaca API calls live here, isolated from the rest of the app: both
the Market Data API and (for the market clock) the trading API.

Three entry points:

- :func:`fetch_summaries` — a single ``GET /v2/stocks/snapshots`` request
  covering every symbol in the batch. Returns both the per-symbol
  :class:`app.schemas.StockSummary` dict *and* each symbol's latest minute
  bar so the caller can persist it to the SQLite backup store.
- :func:`fetch_bars` — ``GET /v2/stocks/{symbol}/bars`` for one symbol at
  one timeframe (``1Min``/``1Hour``/``1Day``), following
  ``next_page_token`` pagination until exhausted.
- :func:`fetch_clock` — ``GET /v2/clock`` on the *trading* API host
  (``app.config.ALPACA_TRADING_BASE_URL``), Alpaca's legacy market clock.
  Same raising contract as ``fetch_bars``: request failure or a malformed
  body raises :class:`AlpacaError` (429 flagged distinctly), never a
  partial/best-effort result.

Fault-tolerance contract:

- Every 200 response is validated against the documented shape before its
  fields are trusted: the body must be a JSON object (not an HTML error
  page, not a bare JSON array/scalar); each bar's ``t`` must be a string
  :func:`normalize_timestamp` can parse and its ``o``/``h``/``l``/``c``/
  ``v``/``vw``/``n`` must all be present and numeric (``bool`` and
  non-finite floats do not count, so a boolean or a numeric string like
  ``"310.85"`` is rejected rather than silently stored as SQLite ``REAL``
  junk). A mismatched top-level ``symbol``, or a ``bars``/
  ``next_page_token`` of the wrong type, is likewise rejected — the
  latter is checked *before* it is followed, so a malformed token can't
  spin the paginator forever. None of this is ever mistaken for rate
  limiting. Unknown extra keys are ignored.
- ``fetch_summaries`` NEVER raises. A request-level failure (network
  error, 429, any non-2xx, or a malformed/non-object body) produces an
  all-error batch — every requested symbol present, ``is_stale=True``,
  ``error`` set — with rate limiting flagged distinctly via
  :data:`RATE_LIMIT_ERROR_PREFIX` so the router's backoff can tell
  "Alpaca is throttling us" apart from "this symbol is broken". A symbol
  missing from an otherwise-good response, a snapshot entry that isn't an
  object, or a present-but-wrong-typed price-path field
  (``latestTrade.p``/``minuteBar.c``/``dailyBar.c``/``prevDailyBar.c``)
  degrades only that symbol to a per-symbol error entry; a missing or
  ``None`` field still falls through to the next fallback exactly as
  before. A malformed ``minuteBar`` on an otherwise-good snapshot keeps
  the summary but is skipped for persistence (logged as a warning) so the
  leaderboard never degrades over a persistence-only field.
- ``fetch_bars`` DOES raise (:class:`AlpacaError`) on request-level
  failure *or* a malformed shape — its caller decides whether SQLite can
  cover for the outage. The whole fetch fails atomically (no partial
  list), even when the malformed bar is on a later page after good ones.
  A legitimately empty bar list (weekend, market closed, thin IEX data,
  ``bars`` null/absent) is NOT an error: it returns ``[]``.

Requests carry the ``APCA-API-KEY-ID`` / ``APCA-API-SECRET-KEY`` headers
(from ``app.config.ALPACA_KEY_ID`` / ``ALPACA_SECRET_KEY``, read at call
time) and ``feed=iex`` (free plan). Bars requests send ``start`` but no
``end`` (Alpaca defaults to "now", which the IEX feed may serve without
the paid plan's 15-minute SIP restriction), plus
``adjustment=<ALPACA_BARS_ADJUSTMENT>`` (default ``split``) so history is
split-adjusted rather than Alpaca's default as-traded ``raw`` prices; the
snapshots request has no adjustment param and never sends one. The clock
request has no ``feed`` param either -- it isn't feed-scoped -- and goes
to the trading host instead of the data host the other two use.

Timestamps are normalized to exactly ``YYYY-MM-DDTHH:MM:SSZ`` (UTC) so
that lexicographic string comparison in SQLite equals chronological order.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

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
TIMEFRAME_DAYS = "1Day"

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


@dataclass(frozen=True)
class MarketClock:
    """Alpaca's market clock (``GET /v2/clock``), timestamps normalised to
    ``YYYY-MM-DDTHH:MM:SSZ``."""

    timestamp: str
    is_open: bool
    next_open: str
    next_close: str


def fetch_clock() -> MarketClock:
    """Fetch Alpaca's legacy market clock: ``GET /v2/clock`` on the
    *trading* API host (``app.config.ALPACA_TRADING_BASE_URL``), never the
    data host. No ``feed`` param -- the clock isn't feed-scoped.

    Same raising contract as :func:`fetch_bars`: a network error or any
    non-2xx response raises :class:`AlpacaError` (429 flagged
    ``is_rate_limit=True`` with the rate-limit message prefix; other
    non-2xx a plain error). The body must be a JSON object with
    ``is_open`` a real ``bool`` (not ``"true"``/``1``/``0``/``None`` --
    those are malformed, never mistaken for rate limiting) and
    ``timestamp``/``next_open``/``next_close`` each a string
    :func:`normalize_timestamp` can parse; a missing/wrong-typed/
    unparseable field raises a malformed :class:`AlpacaError` naming the
    field. Unknown extra keys (``phase``, ``market``, ...) are ignored.
    Returns a :class:`MarketClock` with all three timestamps normalized to
    ``YYYY-MM-DDTHH:MM:SSZ`` UTC.
    """
    from app import config

    try:
        with _client(config.ALPACA_TRADING_BASE_URL) as client:
            response = client.get("/v2/clock")
    except httpx.HTTPError as exc:
        raise AlpacaError(f"{ERROR_PREFIX}: {exc}") from exc

    if response.status_code == 429:
        raise AlpacaError(
            f"{RATE_LIMIT_ERROR_PREFIX}: {response.text}", is_rate_limit=True
        )
    if response.status_code >= 400:
        raise AlpacaError(f"{ERROR_PREFIX}: HTTP {response.status_code}")

    body = _json_object(response)
    if body is None:
        raise AlpacaError(
            f"{ERROR_PREFIX}: malformed response: body is not a JSON object"
        )

    is_open = body.get("is_open")
    if "is_open" not in body or not isinstance(is_open, bool):
        raise AlpacaError(
            f"{ERROR_PREFIX}: malformed response: "
            "field 'is_open' is missing or not a boolean"
        )

    timestamps: dict[str, str] = {}
    for key in ("timestamp", "next_open", "next_close"):
        value = body.get(key)
        if key not in body or not isinstance(value, str):
            raise AlpacaError(
                f"{ERROR_PREFIX}: malformed response: "
                f"field {key!r} is missing or not a string"
            )
        try:
            timestamps[key] = normalize_timestamp(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise AlpacaError(
                f"{ERROR_PREFIX}: malformed response: "
                f"field {key!r} is not a parseable timestamp"
            ) from exc

    return MarketClock(
        timestamp=timestamps["timestamp"],
        is_open=is_open,
        next_open=timestamps["next_open"],
        next_close=timestamps["next_close"],
    )


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
    any UTC offset -- ``+00:00`` (data-API bars) as well as non-UTC
    offsets like ``-04:00`` (the trading-API clock's New York stamps).
    A non-UTC offset is *converted* to the equivalent UTC instant (not
    merely relabelled ``Z``); a naive datetime (no offset at all) is
    treated as already UTC. Output always uses the ``Z`` suffix so string
    ordering equals chronological ordering.
    """
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_object(response: httpx.Response) -> dict | None:
    """Return the parsed JSON body if it's an object, else ``None``.

    ``None`` signals a malformed response: the body isn't valid JSON at all
    (``response.json()`` raises ``ValueError`` — e.g. an HTML maintenance
    page) or it parses to something other than a JSON object (a list, a
    scalar).
    """
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _is_numeric(value: object) -> bool:
    """True for a real, finite number — excludes ``bool`` (a ``bool`` is an
    ``int`` subclass) and non-finite floats (``NaN``/``inf``)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_bar(raw: object, context: str) -> Bar:
    """Parse one Alpaca bar dict into a :class:`Bar`.

    Used by both ``fetch_bars`` and ``fetch_summaries`` (for the snapshot's
    ``minuteBar``). Raises ``ValueError`` — never `KeyError`/`TypeError`/
    `AttributeError` — naming ``context`` and the offending field when
    ``raw`` doesn't match the documented ``{t, o, h, l, c, v, vw, n}``
    shape. Callers decide how to surface the ``ValueError`` (raise an
    :class:`AlpacaError`, or degrade a single entry).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: bar is not an object")

    ts_raw = raw.get("t")
    if not isinstance(ts_raw, str):
        raise ValueError(f"{context}: field 't' is missing or not a string")
    try:
        ts = normalize_timestamp(ts_raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"{context}: field 't' is not a parseable timestamp"
        ) from exc

    fields: dict[str, float] = {}
    for key in ("o", "h", "l", "c", "v", "vw", "n"):
        value = raw.get(key)
        if key not in raw or not _is_numeric(value):
            raise ValueError(f"{context}: field '{key}' is missing or not numeric")
        fields[key] = value

    return Bar(ts=ts, price=fields["c"], analytics=json.dumps(fields))


def _client(base_url: str | None = None) -> httpx.Client:
    """Build an ``httpx.Client`` for an Alpaca API host.

    ``base_url`` defaults to ``app.config.ALPACA_DATA_BASE_URL`` (the
    market data API); pass ``app.config.ALPACA_TRADING_BASE_URL`` for
    trading-API endpoints like ``GET /v2/clock``. Credentials and the
    request timeout (``ALPACA_TIMEOUT_SECONDS``) are read from
    ``app.config`` at call time (monkeypatch-friendly); ``_transport`` is
    injected when set.
    """
    from app import config

    headers = {
        "APCA-API-KEY-ID": config.ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
    }
    kwargs: dict = {
        "base_url": base_url if base_url is not None else config.ALPACA_DATA_BASE_URL,
        "headers": headers,
        "timeout": config.ALPACA_TIMEOUT_SECONDS,
    }
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

    def _malformed_summary(
        symbol: str, name: str, sector: str, detail: str
    ) -> StockSummary:
        return StockSummary(
            ticker=symbol,
            name=name,
            sector=sector,
            price=None,
            currency=USD,
            previous_close=None,
            change=None,
            change_percent=None,
            is_stale=True,
            error=f"{ERROR_PREFIX}: malformed response: {detail}",
        )

    def _numeric_field(parent: object, key: str, context: str) -> float | None:
        """Read ``parent[key]``, treating missing/``None`` as "no value yet
        — fall through to the next fallback" (today's behaviour), but
        raising ``ValueError`` when ``parent`` is present-but-not-an-object
        or the field is present-but-not-numeric."""
        if parent is None:
            return None
        if not isinstance(parent, dict):
            raise ValueError(f"{context} is not an object")
        if key not in parent or parent[key] is None:
            return None
        value = parent[key]
        if not _is_numeric(value):
            raise ValueError(f"{context}.{key} is not numeric")
        return value

    logger = logging.getLogger(__name__)

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

    body = _json_object(response)
    if body is None:
        return _error_batch(
            f"{ERROR_PREFIX}: malformed response: body is not a JSON object"
        )

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

        if not isinstance(snap, dict):
            summaries[symbol] = _malformed_summary(
                symbol, name, sector, "snapshot entry is not an object"
            )
            continue

        latest_trade = snap.get("latestTrade")
        minute_bar = snap.get("minuteBar")
        daily_bar = snap.get("dailyBar")
        prev_daily_bar = snap.get("prevDailyBar")

        try:
            price = _numeric_field(latest_trade, "p", f"{symbol} latestTrade")
            if price is None:
                price = _numeric_field(minute_bar, "c", f"{symbol} minuteBar")
            if price is None:
                price = _numeric_field(daily_bar, "c", f"{symbol} dailyBar")
            previous_close = _numeric_field(
                prev_daily_bar, "c", f"{symbol} prevDailyBar"
            )
        except ValueError as exc:
            summaries[symbol] = _malformed_summary(symbol, name, sector, str(exc))
            continue

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
            try:
                minute_bars[symbol] = _parse_bar(minute_bar, f"{symbol} minuteBar")
            except ValueError as exc:
                logger.warning(
                    "alpaca: skipping malformed minuteBar for %s: %s", symbol, exc
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
                "adjustment": config.ALPACA_BARS_ADJUSTMENT,
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

            body = _json_object(response)
            if body is None:
                raise AlpacaError(
                    f"{ERROR_PREFIX}: malformed response: body is not a JSON object"
                )

            body_symbol = body.get("symbol")
            if body_symbol is not None and body_symbol != symbol:
                raise AlpacaError(
                    f"{ERROR_PREFIX}: malformed response: symbol {body_symbol!r} "
                    f"does not match requested {symbol!r}"
                )

            raw_bars = body.get("bars")
            if raw_bars is not None and not isinstance(raw_bars, list):
                raise AlpacaError(
                    f"{ERROR_PREFIX}: malformed response: 'bars' is not a list"
                )

            for raw_bar in raw_bars or []:
                try:
                    bars.append(
                        _parse_bar(raw_bar, f"bar in {symbol} bars response")
                    )
                except ValueError as exc:
                    raise AlpacaError(
                        f"{ERROR_PREFIX}: malformed response: {exc}"
                    ) from exc

            next_token = body.get("next_page_token")
            if next_token is not None and not isinstance(next_token, str):
                raise AlpacaError(
                    f"{ERROR_PREFIX}: malformed response: "
                    "'next_page_token' is not a string"
                )
            page_token = next_token
            if not page_token:
                break

    return bars
