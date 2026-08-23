#!/usr/bin/env python3
"""Mock Alpaca Market Data server for offline development.

Serves a deterministic sinusoidal price for every symbol:

    price(t) = OFFSET + AMPLITUDE * sin(2*pi * t / PERIOD + phase(symbol))

with defaults OFFSET=100, AMPLITUDE=20, PERIOD=20 minutes -- i.e. every
stock oscillates between 80 and 120 on a 20-minute cycle. Each of the app's
20 symbols gets a different phase (spread evenly round the circle) so the
leaderboard keeps re-ranking; pass --no-phase-shift to make them identical.

Implements exactly the three endpoints ``backend/app/alpaca_client.py`` calls,
with Alpaca's response shapes:

    GET /v2/stocks/snapshots?symbols=A,B,...&feed=iex
        -> {"AAPL": {"latestTrade": {...}, "minuteBar": {...},
                     "dailyBar": {...}, "prevDailyBar": {...}}, ...}

    GET /v2/stocks/{symbol}/bars?timeframe=1Min|1Hour|1Day&start=...&limit=N&page_token=T
        -> {"symbol": "...", "bars": [{"t","o","h","l","c","v","vw","n"}, ...],
            "next_page_token": "..." | null}

    GET /v2/clock  (Alpaca's legacy trading clock)
        -> {"timestamp": "...", "is_open": bool,
            "next_open": "...", "next_close": "..."}

Minute/hour bars are generated 24/7 (the mock market never closes) unless
``--sessions-only`` is passed, which emits them only inside the 13:30Z-20:00Z
session (every day, weekends included) so the intraday chart has a real
market-closed hole to grey out; daily bars are one per weekday stamped at
04:00Z like Alpaca's. The ``/v2/clock`` session, by contrast, always runs
13:30Z-20:00Z every day (weekends included) in BOTH the default and
``--sessions-only`` modes, independent of the bars flag, so the dev banner
can show both an open and a closed state. Auth headers and
``feed`` / ``adjustment`` are accepted and ignored. Stdlib only.

The backend needs to be pointed at this mock for BOTH
``ALPACA_DATA_BASE_URL`` (snapshots/bars) and ``ALPACA_TRADING_BASE_URL``
(clock) -- both point at the same host:port below.

    python3 mock/alpaca_mock.py --port 8500
    ALPACA_DATA_BASE_URL=http://localhost:8500 \\
    ALPACA_TRADING_BASE_URL=http://localhost:8500 \\
    uv run uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# The app's fixed universe, in order: used only to hand out evenly spaced
# phases. Unknown symbols get a phase derived from their name.
KNOWN_SYMBOLS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "AVGO",
    "BRK.B",
    "JPM",
    "V",
    "LLY",
    "UNH",
    "XOM",
    "MA",
    "HD",
    "PG",
    "COST",
    "JNJ",
    "NFLX",
]

# ── Price model (set from CLI in main()) ───────────────────────────────────────

OFFSET = 100.0
AMPLITUDE = 20.0
PERIOD_SECONDS = 0.0
PHASE_SHIFT = True
SESSIONS_ONLY = False

MINUTE = 60
HOUR = 3600
DAY = 86400

# Alpaca timeframe strings -> bar duration in seconds.
TIMEFRAMES = {"1Min": MINUTE, "1Hour": HOUR, "1Day": DAY}

# Daily bars: stamped at 04:00Z (midnight New York, like Alpaca), open at the
# 13:30Z session open, close at 20:00Z.
DAILY_STAMP_HOUR = 4
SESSION_OPEN = (13, 30)
SESSION_CLOSE = (20, 0)


def phase_for(symbol: str) -> float:
    if not PHASE_SHIFT:
        return 0.0
    if symbol in KNOWN_SYMBOLS:
        return 2 * math.pi * KNOWN_SYMBOLS.index(symbol) / len(KNOWN_SYMBOLS)
    return 2 * math.pi * (sum(map(ord, symbol)) % 360) / 360


def price_at(symbol: str, t: datetime) -> float:
    x = 2 * math.pi * t.timestamp() / PERIOD_SECONDS + phase_for(symbol)
    return round(OFFSET + AMPLITUDE * math.sin(x), 4)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(raw: str) -> datetime:
    raw = raw.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def bar(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    open_at: datetime | None = None,
    close_at: datetime | None = None,
) -> dict:
    """One OHLCV bar over [start, end). h/l are exact for the sine.

    The extrema are computed analytically -- the endpoints plus any peak
    (sin = +1) or trough (sin = -1) that falls inside the bar -- so a bar is
    O(1) regardless of how long it is relative to the period. (Sampling the
    interval instead made a 1Day request take ~5 s with a multi-day period,
    which tripped the backend's Alpaca timeout.)
    """
    o = price_at(symbol, open_at or start)
    c = price_at(symbol, close_at or end)
    h, lo = _extrema(symbol, start, end)
    return {
        "t": iso(start),
        "o": o,
        "h": h,
        "l": lo,
        "c": c,
        "v": 1000,
        "vw": round((o + c) / 2, 4),
        "n": 10,
    }


def _extrema(symbol: str, start: datetime, end: datetime) -> tuple[float, float]:
    """(high, low) of the price curve over [start, end], exact for the sine.

    The phase argument is ``x = 2*pi*t/PERIOD + phase``; the curve peaks at
    ``x = pi/2 + 2*pi*k`` and troughs at ``x = 3*pi/2 + 2*pi*k``. Check both
    endpoints and the first peak/trough at or after ``start``: if it lands
    before ``end`` the extreme is reached inside the bar.
    """
    x0 = 2 * math.pi * start.timestamp() / PERIOD_SECONDS + phase_for(symbol)
    x1 = 2 * math.pi * end.timestamp() / PERIOD_SECONDS + phase_for(symbol)
    lo, hi = sorted((price_at(symbol, start), price_at(symbol, end)))
    two_pi = 2 * math.pi
    next_peak = math.pi / 2 + two_pi * math.ceil((x0 - math.pi / 2) / two_pi)
    if next_peak <= x1:
        hi = round(OFFSET + AMPLITUDE, 4)
    next_trough = 3 * math.pi / 2 + two_pi * math.ceil((x0 - 3 * math.pi / 2) / two_pi)
    if next_trough <= x1:
        lo = round(OFFSET - AMPLITUDE, 4)
    return hi, lo


def daily_bar(symbol: str, day: datetime) -> dict:
    d = day.replace(hour=0, minute=0, second=0, microsecond=0)
    stamp = d.replace(hour=DAILY_STAMP_HOUR)
    return bar(
        symbol,
        stamp,
        stamp + timedelta(days=1),
        open_at=d.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1]),
        close_at=d.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1]),
    )


def previous_weekday(day: datetime) -> datetime:
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def floor_to(t: datetime, seconds: int) -> datetime:
    epoch = int(t.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


# ── Endpoint logic ─────────────────────────────────────────────────────────────


def snapshots(symbols: list[str], now: datetime) -> dict:
    out = {}
    minute_start = floor_to(now, MINUTE)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for symbol in symbols:
        price = price_at(symbol, now)
        out[symbol] = {
            "latestTrade": {
                "t": iso(now),
                "p": price,
                "s": 100,
                "x": "V",
                "i": 1,
                "c": ["@"],
                "z": "C",
            },
            "latestQuote": {
                "t": iso(now),
                "ap": round(price + 0.01, 4),
                "bp": round(price - 0.01, 4),
                "as": 1,
                "bs": 1,
                "ax": "V",
                "bx": "V",
            },
            "minuteBar": bar(
                symbol,
                minute_start,
                minute_start + timedelta(seconds=MINUTE),
                close_at=now,
            ),
            "dailyBar": daily_bar(symbol, today) | {"c": price},
            "prevDailyBar": daily_bar(symbol, previous_weekday(today)),
        }
    return out


def bars(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    limit: int,
    page_token: str | None,
) -> dict:
    duration = TIMEFRAMES[timeframe]
    rows: list[dict] = []
    if duration == DAY:
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        if start > day.replace(hour=DAILY_STAMP_HOUR):
            day += timedelta(days=1)
        while day.replace(hour=DAILY_STAMP_HOUR) <= end:
            if day.weekday() < 5:
                b = daily_bar(symbol, day)
                if day.date() == end.date():  # today: close so far
                    b["c"] = price_at(symbol, end)
                rows.append(b)
            day += timedelta(days=1)
    else:
        t = floor_to(start, duration)
        if t < start:
            t += timedelta(seconds=duration)
        while t <= end:
            bar_end = min(t + timedelta(seconds=duration), end)
            if not SESSIONS_ONLY or SESSION_OPEN <= (t.hour, t.minute) < SESSION_CLOSE:
                rows.append(
                    bar(symbol, t, t + timedelta(seconds=duration), close_at=bar_end)
                )
            t += timedelta(seconds=duration)

    offset = int(page_token) if page_token else 0
    page = rows[offset : offset + limit]
    next_offset = offset + limit
    return {
        "symbol": symbol,
        "bars": page,
        "next_page_token": str(next_offset) if next_offset < len(rows) else None,
    }


def clock(now: datetime) -> dict:
    """Alpaca's legacy trading-clock body.

    The clock session is always 13:30Z-20:00Z every day (weekends included),
    regardless of --sessions-only -- unlike bars, which only observe that
    window when --sessions-only is passed. This keeps the dev banner able to
    show both an open and a closed state in either mode.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    open_today = today.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1])
    close_today = today.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1])
    is_open = SESSION_OPEN <= (now.hour, now.minute) < SESSION_CLOSE
    next_open = open_today if now < open_today else open_today + timedelta(days=1)
    next_close = close_today if now < close_today else close_today + timedelta(days=1)
    return {
        "timestamp": iso(now),
        "is_open": is_open,
        "next_open": iso(next_open),
        "next_close": iso(next_close),
    }


# ── HTTP plumbing ──────────────────────────────────────────────────────────────

BARS_PATH = re.compile(r"^/v2/stocks/([^/]+)/bars$")


class Handler(BaseHTTPRequestHandler):
    server_version = "MockAlpaca/1.0"

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # The client (the backend's 5 s Alpaca timeout) gave up before
            # we finished writing; nothing to do but move on quietly.
            pass

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        url = urlparse(self.path)
        qs = {k: v[-1] for k, v in parse_qs(url.query).items()}
        now = datetime.now(timezone.utc)
        try:
            if url.path == "/v2/stocks/snapshots":
                symbols = [s for s in qs.get("symbols", "").split(",") if s]
                if not symbols:
                    return self._send(400, {"message": "symbols is required"})
                return self._send(200, snapshots(symbols, now))

            m = BARS_PATH.match(url.path)
            if m:
                timeframe = qs.get("timeframe", "1Day")
                if timeframe not in TIMEFRAMES:
                    return self._send(
                        400, {"message": f"unsupported timeframe {timeframe}"}
                    )
                start = (
                    parse_iso(qs["start"]) if "start" in qs else now - timedelta(days=1)
                )
                end = parse_iso(qs["end"]) if "end" in qs else now
                limit = max(1, min(int(qs.get("limit", "1000")), 10000))
                return self._send(
                    200,
                    bars(
                        m.group(1), timeframe, start, end, limit, qs.get("page_token")
                    ),
                )

            if url.path == "/v2/clock":
                return self._send(200, clock(now))

            if url.path in ("/", "/healthz"):
                return self._send(
                    200,
                    {
                        "status": "ok",
                        "model": "sine",
                        "offset": OFFSET,
                        "amplitude": AMPLITUDE,
                        "period_seconds": PERIOD_SECONDS,
                        "phase_shift": PHASE_SHIFT,
                        "sessions_only": SESSIONS_ONLY,
                        "clock_session": "13:30Z-20:00Z",
                    },
                )

            return self._send(404, {"message": f"no route for {url.path}"})
        except (KeyError, ValueError) as exc:
            return self._send(400, {"message": f"bad request: {exc}"})

    def log_message(self, fmt: str, *args) -> None:  # quieter, to stderr
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> None:
    global OFFSET, AMPLITUDE, PERIOD_SECONDS, PHASE_SHIFT, SESSIONS_ONLY
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument(
        "--offset", type=float, default=100.0, help="y-axis shift (default 100)"
    )
    parser.add_argument(
        "--amplitude", type=float, default=20.0, help="sine amplitude (default 20)"
    )
    parser.add_argument(
        "--period-minutes", type=float, default=2000.0, help="period (default 20)"
    )
    parser.add_argument(
        "--sessions-only",
        action="store_true",
        help="only emit minute/hour bars inside the 13:30Z-20:00Z session "
        "(every day, weekends included) so the 1D chart has a market-closed hole",
    )
    parser.add_argument(
        "--no-phase-shift",
        action="store_true",
        help="give every symbol the same phase (identical curves)",
    )
    args = parser.parse_args()

    OFFSET, AMPLITUDE = args.offset, args.amplitude
    PERIOD_SECONDS = args.period_minutes * 60.0
    PHASE_SHIFT = not args.no_phase_shift
    SESSIONS_ONLY = args.sessions_only

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        f"mock alpaca on http://{args.host}:{args.port}  "
        f"price = {OFFSET} + {AMPLITUDE}*sin(2*pi*t/{args.period_minutes}min"
        f"{' + phase(symbol)' if PHASE_SHIFT else ''})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
