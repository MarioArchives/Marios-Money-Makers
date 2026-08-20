# UK Stocks Dashboard

Local app showing near-live data for 20 UK-listed companies via `yfinance`. See
`docs/ptb/uk-stocks-dashboard.md` for the full design/plan and build status.

## Run with Docker Compose (recommended for repeatability)

```
docker compose up --build
```

Frontend: http://localhost:5173  Backend: http://localhost:8000

## Run locally (two terminals)

```
# terminal 1
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend
npm install
npm run dev
```

## Tests

```
cd backend && uv run pytest
cd frontend && npm run test
```
