import os

# Cache TTLs. These are deliberately long: yfinance/Yahoo rate-limits by IP,
# and the frontend polls every 20s, so the cache -- not the poll interval --
# is what determines how often we actually talk to Yahoo.
CACHE_TTL_SECONDS = float(os.environ.get("CACHE_TTL_SECONDS", "90"))
HISTORY_CACHE_TTL_SECONDS = float(os.environ.get("HISTORY_CACHE_TTL_SECONDS", "120"))

# Exponential backoff applied between refresh attempts after a *total*
# batch failure (e.g. every ticker rate-limited). The n-th consecutive
# failure blocks fetches for `BACKOFF_BASE_SECONDS * 2 ** (n - 1)` seconds,
# capped at BACKOFF_MAX_SECONDS. Reset on the first successful batch.
BACKOFF_BASE_SECONDS = float(
    os.environ.get("BACKOFF_BASE_SECONDS", str(CACHE_TTL_SECONDS))
)
BACKOFF_MAX_SECONDS = float(os.environ.get("BACKOFF_MAX_SECONDS", "600"))

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
