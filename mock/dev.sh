#!/usr/bin/env bash
# Run the backend locally against the mock Alpaca server (no real keys
# needed): ./mock/dev.sh (mock :8500 + backend :8000, Ctrl-C stops both).
# See README.md ("Offline, against the mock") for details.
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
