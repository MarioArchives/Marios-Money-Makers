"""All Alpaca API calls live here: the Market Data API, plus the trading
API for the market clock. See README.md for the full validation contract.
``fetch_summaries`` never raises; ``fetch_bars``/``fetch_clock`` do.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.schemas import StockSummary

USD = "USD"

# Rate-limit prefix lets callers tell "Alpaca is throttling us" apart from
# "this symbol is broken".
ERROR_PREFIX = "alpaca error"
RATE_LIMIT_ERROR_PREFIX = "alpaca rate limited"

TIMEFRAME_MINUTE = "1Min"
TIMEFRAME_HOUR = "1Hour"
TIMEFRAME_DAYS = "1Day"

# Test seam: when set, `_client()` builds on this transport (no network).
_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class Bar:
    """One price bar; ``ts`` is ``YYYY-MM-DDTHH:MM:SSZ``, ``price`` is the close."""

    ts: str
    price: float
    analytics: str


@dataclass(frozen=True)
class MarketClock:
    """Alpaca's market clock; timestamps normalised to ``YYYY-MM-DDTHH:MM:SSZ``."""

    timestamp: str
    is_open: bool
    next_open: str
    next_close: str


def fetch_clock() -> MarketClock:
    """Fetch Alpaca's market clock (``GET /v2/clock``, trading API host).

    Raises :class:`AlpacaError` on request failure or a malformed body
    (429 flagged ``is_rate_limit=True``); never a partial result.
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
    """Normalize an Alpaca RFC3339 timestamp to exactly ``YYYY-MM-DDTHH:MM:SSZ``
    UTC, so lexicographic string comparison equals chronological order."""
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_object(response: httpx.Response) -> dict | None:
    """Return the parsed JSON body if it's an object, else ``None`` (malformed)."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _is_numeric(value: object) -> bool:
    """True for a real, finite number — excludes ``bool`` and ``NaN``/``inf``."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_bar(raw: object, context: str) -> Bar:
    """Parse one Alpaca bar dict into a :class:`Bar`; raises ``ValueError``
    (never ``KeyError``/``TypeError``) naming ``context`` and the bad field."""
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
    """Build an ``httpx.Client`` for an Alpaca API host; credentials and
    timeout are read from ``app.config`` at call time so tests can monkeypatch."""
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

    NEVER raises: a request-level failure returns an all-error batch
    (every symbol present, ``is_stale=True``) instead of raising.
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
        """Missing/``None`` falls through to the next price fallback;
        present-but-wrong-typed raises ``ValueError`` (a real error)."""
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

    Raises :class:`AlpacaError` and fails atomically on any request-level
    or shape failure; a legitimately empty result returns ``[]``.
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
