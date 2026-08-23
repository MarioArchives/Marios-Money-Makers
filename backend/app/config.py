import os
from pathlib import Path

# Leaderboard freshness window: the `summaries` table (stamped in
# `fetch_log`) is served without calling Alpaca while its last successful
# snapshots fetch is younger than this. Matches the frontend's 20s poll so
# a poll only hits Alpaca when the stored batch is older than one poll
# interval (one bulk snapshot call per ~20s per database is well within
# the free tier's 200 req/min).
CACHE_TTL_SECONDS = float(os.environ.get("CACHE_TTL_SECONDS", "20"))
HISTORY_CACHE_TTL_SECONDS = float(os.environ.get("HISTORY_CACHE_TTL_SECONDS", "120"))

# Exponential backoff applied between refresh attempts after a *total*
# batch failure (e.g. every ticker rate-limited). The n-th consecutive
# failure blocks fetches for `BACKOFF_BASE_SECONDS * 2 ** (n - 1)` seconds,
# capped at BACKOFF_MAX_SECONDS. Reset on the first successful batch.
# Fixed default (not derived from CACHE_TTL_SECONDS): the backoff window
# must stay longer than a few frontend polls so repeated 20s polls during
# an outage keep serving stale data instead of hammering Alpaca.
BACKOFF_BASE_SECONDS = float(os.environ.get("BACKOFF_BASE_SECONDS", "90"))
BACKOFF_MAX_SECONDS = float(os.environ.get("BACKOFF_MAX_SECONDS", "600"))

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

# --- Alpaca Market Data API -------------------------------------------------
# Credentials use the same env var names `.secrets.sh` exports; they map to
# the APCA-API-KEY-ID / APCA-API-SECRET-KEY request headers.
ALPACA_KEY_ID = os.environ.get("KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("SECRET", "")
ALPACA_DATA_BASE_URL = os.environ.get(
    "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
)
# The market clock (`GET /v2/clock`) lives on the *trading* API host, not
# the data host above. Defaults to the paper-trading host, since the
# README walks users through setting up a paper account; live keys should
# set ALPACA_TRADING_BASE_URL=https://api.alpaca.markets.
ALPACA_TRADING_BASE_URL = os.environ.get(
    "ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets"
)
# Free-plan keys only have access to the IEX feed.
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")
# `adjustment` query param sent on every bars request (raw|split|dividend|all).
# Alpaca's own default is `raw` (as-traded prices), which draws a stock
# split as a price cliff; `split` makes the stored history split-adjusted
# like every consumer charting site. Not sent on the snapshots request
# (that endpoint has no adjustment param). Read via `config.` at call time;
# `storage.ensure_bars_adjustment` invalidates the bar `fetch_log` stamps
# once whenever this value changes so already-stored bars get refetched.
ALPACA_BARS_ADJUSTMENT = os.environ.get("ALPACA_BARS_ADJUSTMENT", "split")
# Explicit httpx timeout (seconds, connect+read+write+pool) for every
# Alpaca request.
ALPACA_TIMEOUT_SECONDS = float(os.environ.get("ALPACA_TIMEOUT_SECONDS", "5.0"))

# --- SQLite backup store ----------------------------------------------------
# Read via `app.config.DB_PATH` at call time (never `from ... import`), so
# tests can monkeypatch this single attribute to point at a tmp DB.
DB_PATH = os.environ.get(
    "STOCKS_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "stocks.db"),
)

# Per-tier "serve straight from SQLite without calling Alpaca" windows.
FRESHNESS_MINUTE_SECONDS = float(os.environ.get("FRESHNESS_MINUTE_SECONDS", "20"))
FRESHNESS_HOUR_SECONDS = float(os.environ.get("FRESHNESS_HOUR_SECONDS", "3600"))
FRESHNESS_DAY_SECONDS = float(os.environ.get("FRESHNESS_DAY_SECONDS", "86400"))

# Retention windows / backfill depth for the three bar tables. The
# "days" tier (daily bars) is never pruned and backs the all-time view:
# the first fetch backfills DAY_BACKFILL_DAYS of daily bars, after which
# the stored series only ever grows.
MINUTE_RETENTION_HOURS = 24
HOUR_RETENTION_DAYS = 30
DAY_BACKFILL_DAYS = int(os.environ.get("DAY_BACKFILL_DAYS", "365"))

# --- Background backfill sweep ----------------------------------------------
# On startup, and then every BACKFILL_INTERVAL_SECONDS, `app.backfill.sweep`
# walks every (tier, ticker) pair and refreshes the ones whose `fetch_log`
# entry has lapsed past the tier's freshness window, so the bar tables are
# populated without anyone opening a chart (e.g. after the backend was down
# for a while). Steady-state cost: each pass only refetches lapsed tiers, so
# with the defaults that is ~20 minute-tier calls per interval, ~20
# hour-tier calls per hour and ~20 daily calls per day; the worst case after
# a long gap is all 60 pairs in one pass -- well under the free tier's
# 200 req/min. Set BACKFILL_ENABLED=0 (or "false") to disable. Read via
# `app.config.<NAME>` at call time so tests can monkeypatch.
BACKFILL_ENABLED = os.environ.get("BACKFILL_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
    "",
}
BACKFILL_INTERVAL_SECONDS = float(os.environ.get("BACKFILL_INTERVAL_SECONDS", "600"))
# How long a sweep pauses after Alpaca rate-limits a fetch before retrying
# that same pair once more and then moving on.
BACKFILL_RATE_LIMIT_PAUSE_SECONDS = float(
    os.environ.get("BACKFILL_RATE_LIMIT_PAUSE_SECONDS", "60")
)
