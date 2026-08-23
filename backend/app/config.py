import os
from pathlib import Path

# Leaderboard freshness window; matches the frontend's 20s poll cadence.
CACHE_TTL_SECONDS = float(os.environ.get("CACHE_TTL_SECONDS", "20"))
HISTORY_CACHE_TTL_SECONDS = float(os.environ.get("HISTORY_CACHE_TTL_SECONDS", "120"))

# Exponential backoff after a total batch failure: n-th consecutive failure
# blocks fetches for BACKOFF_BASE_SECONDS * 2 ** (n - 1), capped at
# BACKOFF_MAX_SECONDS, reset on the first successful batch.
BACKOFF_BASE_SECONDS = float(os.environ.get("BACKOFF_BASE_SECONDS", "90"))
BACKOFF_MAX_SECONDS = float(os.environ.get("BACKOFF_MAX_SECONDS", "600"))

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

# Credentials use the same env var names `.secrets.sh` exports.
ALPACA_KEY_ID = os.environ.get("KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("SECRET", "")
ALPACA_DATA_BASE_URL = os.environ.get(
    "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
)
# The market clock (`GET /v2/clock`) lives on the trading API host, not the
# data host above; defaults to paper trading.
ALPACA_TRADING_BASE_URL = os.environ.get(
    "ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets"
)
# Free-plan keys only have access to the IEX feed.
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")
# Bars `adjustment` query param; not sent on the snapshots request. Read via
# `config.` at call time so tests can monkeypatch it.
ALPACA_BARS_ADJUSTMENT = os.environ.get("ALPACA_BARS_ADJUSTMENT", "split")
ALPACA_TIMEOUT_SECONDS = float(os.environ.get("ALPACA_TIMEOUT_SECONDS", "5.0"))

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

# Retention windows / backfill depth for the three bar tables; daily bars are
# never pruned.
MINUTE_RETENTION_HOURS = 24
HOUR_RETENTION_DAYS = 30
DAY_BACKFILL_DAYS = int(os.environ.get("DAY_BACKFILL_DAYS", "365"))

# Read via `app.config.<NAME>` at call time so tests can monkeypatch.
BACKFILL_ENABLED = os.environ.get("BACKFILL_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
    "",
}
BACKFILL_INTERVAL_SECONDS = float(os.environ.get("BACKFILL_INTERVAL_SECONDS", "600"))
BACKFILL_RATE_LIMIT_PAUSE_SECONDS = float(
    os.environ.get("BACKFILL_RATE_LIMIT_PAUSE_SECONDS", "60")
)
