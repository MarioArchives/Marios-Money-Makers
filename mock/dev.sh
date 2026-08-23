#!/usr/bin/env bash
# Run the backend locally against the mock Alpaca server (no real keys needed).
#
#   ./mock/dev.sh            # mock on :8500 + backend on :8000 (Ctrl-C stops both)
#   MOCK_PORT=9000 ./mock/dev.sh
#
# The mock serves both the data endpoints (snapshots/bars) and the legacy
# trading clock, so the backend is pointed at it for both
# ALPACA_DATA_BASE_URL and ALPACA_TRADING_BASE_URL.
#
# Uses a separate SQLite file (backend/data/mock.db) so real data in
# stocks.db is untouched. Start the frontend as usual (cd frontend && npm run dev).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOCK_PORT="${MOCK_PORT:-8500}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

python3 "$ROOT/mock/alpaca_mock.py" --port "$MOCK_PORT" "$@" &
MOCK_PID=$!
trap 'kill "$MOCK_PID" 2>/dev/null || true' EXIT

cd "$ROOT/backend"
ALPACA_DATA_BASE_URL="http://localhost:$MOCK_PORT" \
ALPACA_TRADING_BASE_URL="http://localhost:$MOCK_PORT" \
STOCKS_DB_PATH="$ROOT/backend/data/mock.db" \
KEY_ID="${KEY_ID:-mock}" SECRET="${SECRET:-mock}" \
exec uv run uvicorn app.main:app --port "$BACKEND_PORT" --reload
