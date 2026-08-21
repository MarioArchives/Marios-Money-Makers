# Mario's Money Makers (M³)

Local stock dashboard for a fixed universe of **20 US large caps** (AAPL, MSFT,
NVDA, … NFLX), fed by the **Alpaca Market Data API** (free plan, IEX feed) and
backed by a **SQLite** store so the app keeps serving the last known data when
Alpaca is slow, rate-limited, or down.

- **Backend** — Python 3.11+ / FastAPI. SQLite-first everywhere: the
  leaderboard is one batched snapshot call per ~20 s *per database* into a
  20-row `summaries` table (no in-memory cache; survives restarts; single
  flight across requests and processes via locks + a DB lease), exponential
  backoff on total failure, history in three tiers (minute / hour / daily).
- **Frontend** — Vite + React 18 + TypeScript, TanStack Query polling every 20 s,
  `react-router-dom`, Recharts. Three routes: leaderboard (`/`), stock detail
  (`/stock/:ticker`), placeholder dashboard (`/dashboard`). **All prices are
  displayed in GBP**, converted in the browser at the ECB USD→GBP reference
  rate (Frankfurter API).

> The project started life as a *UK Stocks Dashboard* on `yfinance`. The
> original plan/design log lives in `docs/ptb/uk-stocks-dashboard.md`; it
> predates the Alpaca migration and is kept as history, not as current spec.

---

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | ≥ 3.11 | managed via [`uv`](https://docs.astral.sh/uv/) |
| `uv` | any recent | `pip install uv` or the official installer |
| Node.js | 22 LTS | matches `frontend/Dockerfile` |
| Docker + Compose | optional | only for the one-command run |
| Alpaca account | free | create a **paper** account and generate API keys at <https://app.alpaca.markets> |

## Secrets

The backend reads Alpaca credentials from two environment variables:

| Variable | Sent as header |
| --- | --- |
| `KEY_ID` | `APCA-API-KEY-ID` |
| `SECRET` | `APCA-API-SECRET-KEY` |

Copy the template and fill in your keys (`.secrets.sh` is git-ignored — never
commit it):

```bash
cp .secrets.example.sh .secrets.sh   # then edit KEY_ID / SECRET
```

and `source .secrets.sh` in any shell that runs the backend or `docker compose`.
Without keys the backend still starts, but every Alpaca call fails and the
leaderboard renders greyed-out/stale rows.

---

## Run with Docker Compose (recommended)

```bash
source .secrets.sh
docker compose up --build
```

- Frontend: <http://localhost:5173>
- Backend:  <http://localhost:8000> (OpenAPI docs at `/docs`)

Both services mount their source directories for hot reload. The SQLite store
lives in the named volume `stocks-data` (mounted at `/app/data` in the backend
container) so it survives container rebuilds.

## Run locally (two terminals)

```bash
# terminal 1 — backend
source .secrets.sh
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# terminal 2 — frontend
cd frontend
npm install
npm run dev            # -> http://localhost:5173
```

The frontend talks to `VITE_API_BASE_URL` (defaults to `http://localhost:8000`,
set it in `frontend/.env.local` if you change the backend port). Locally the
SQLite file is created at `backend/data/stocks.db` (git-ignored).

---

## Tests

```bash
cd backend  && uv run pytest        # 116 tests — Alpaca mocked via httpx.MockTransport, SQLite in tmp dirs
cd frontend && npm run test         # 203 vitest tests — API and FX mocked, no network
cd frontend && npm run build        # tsc -b type-check + vite production build
```

No test touches the network or your real credentials.

---

## API

All endpoints are `GET`, JSON, CORS-restricted to `ALLOWED_ORIGINS`.

| Endpoint | Returns | Failure behaviour |
| --- | --- | --- |
| `/api/stocks` | `StocksResponse` — all 20 tickers (`price`, `previous_close`, `change`, `change_percent`, `currency`, `is_stale`, `error`) | Always 200. DB-first: served straight from the SQLite `summaries` table (one current-state row per ticker, 20 rows, restart-safe; `change` derived on read) while its `fetch_log` stamp is within `CACHE_TTL_SECONDS`; otherwise exactly one worker refetches (in-process lock + cross-process `fetch_claims` lease) and rewrites all 20 rows atomically. Per-ticker failures are flagged, never fatal. On a *total* failure nothing is written and the table is served marked `is_stale` (cold start with an empty table: the error-flagged batch). |
| `/api/stocks/{ticker}` | one `StockSummary` from the same table/batch | 404 for tickers outside the fixed 20 |
| `/api/stocks/{ticker}/history?range=1d\|30d\|all` | `HistoryResponse` — `points: [{t, close}]` at minute / hourly / daily resolution. `all` is the **all-time** view: every daily bar ever stored for the ticker (the daily tier is never pruned, so it grows by one bar per trading day; the first fetch backfills `MONTH_BACKFILL_DAYS`). | SQLite is the cache: served straight from the DB while the last successful Alpaca fetch for that ticker+tier (tracked in the `fetch_log` table, restart-safe) is within the tier's freshness window; on Alpaca failure returns stored rows with `is_stale: true`; 503 only when the DB has nothing for that ticker+tier. 422 for an unknown `range`. |
| `/api/stocks/{ticker}/stored?tier=minute\|hour\|month` | `StoredDataResponse` — raw inspection of the SQLite store: every row of `bars_<tier>` for the ticker (`ts`, `price`, parsed `analytics` = Alpaca `o/h/l/c/v/vw/n`, `recorded_at`), oldest first, plus `counts` per tier and the tier's `last_fetch_at`. | Read-only, never calls Alpaca. 404 unknown ticker, 422 unknown tier; an empty tier is `200` with `rows: []`. |

`fetch_summaries` makes **one** `GET /v2/stocks/snapshots` request for the
whole universe; history uses `GET /v2/stocks/{symbol}/bars` with pagination.
Every request is capped at `ALPACA_TIMEOUT_SECONDS`. Timestamps are
normalised to `YYYY-MM-DDTHH:MM:SSZ` so string order == chronological order
in SQLite.

**Leaderboard data flow.** There is no in-memory cache. A poll reads the
`fetch_log` stamp `(summaries, *)`; inside `CACHE_TTL_SECONDS` the
`summaries` table is returned with zero Alpaca calls. Otherwise the request
takes the in-process `summaries` lock, re-checks (a burst of concurrent
polls collapses into one fetch; if the leader just failed, the waiters serve
stale instead of retrying), then claims the `(summaries, *)` row of the
`fetch_claims` table -- a lease of `FETCH_LEASE_SECONDS` that makes several
backend processes sharing one DB file fetch at most once per window too (a
process that finds the lease held just serves the table). The claim carries a
`claim_id` fencing token, so a worker that overran its lease and was taken
over cannot delete the new holder's claim when it releases. The fetched batch
is written as 20 rows + the stamp in one transaction, monotonic on
`fetched_at`, so a late/duplicate writer can never overwrite newer data. The
backfill sweep runs the same refresh first on every pass, so a restarted
backend has a populated board before any browser polls.

**Backfill sweep.** Opening a chart is not the only thing that refreshes
the bar tables: on startup, and then every `BACKFILL_INTERVAL_SECONDS`
(default 10 min), `app/backfill.py` walks every (tier, ticker) pair
tier-major (minute → hour → daily) and refreshes any whose `fetch_log`
entry has lapsed past the tier's freshness window, through the same
per-pair lock + fetch + upsert + `fetch_log` + prune path the history
endpoint uses (`refresh_history`). Fresh pairs cost zero Alpaca calls, so
steady state is ~20 minute-bar calls per interval, ~20 hourly per hour and
~20 daily per day (worst case after a long outage: all 60 in one pass). A
rate-limited fetch pauses `BACKFILL_RATE_LIMIT_PAUSE_SECONDS` and retries
that pair once; any other failure is logged (`app.backfill`) and skipped.
Set `BACKFILL_ENABLED=0` to turn it off.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `KEY_ID` / `SECRET` | — | Alpaca credentials (see [Secrets](#secrets)) |
| `ALPACA_DATA_BASE_URL` | `https://data.alpaca.markets` | data API host |
| `ALPACA_FEED` | `iex` | free-plan feed |
| `ALPACA_BARS_ADJUSTMENT` | `split` | `adjustment` sent on every bars request (`raw`\|`split`\|`dividend`\|`all`); `split` keeps history split-adjusted instead of Alpaca's as-traded `raw` default. Changing it invalidates the stored bar fetch stamps once so existing rows are refetched (snapshots unaffected) |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | comma-separated CORS origins |
| `ALPACA_TIMEOUT_SECONDS` | `5.0` | httpx timeout for every Alpaca request |
| `CACHE_TTL_SECONDS` | `20` | leaderboard freshness: serve the `summaries` table without calling Alpaca while its last fetch is younger than this |
| `FETCH_LEASE_SECONDS` | `10` | cross-process fetch lease (`fetch_claims`); keep ≥ `ALPACA_TIMEOUT_SECONDS` |
| `BACKOFF_BASE_SECONDS` / `BACKOFF_MAX_SECONDS` | `90` / `600` | exponential backoff after a total batch failure |
| `STOCKS_DB_PATH` | `backend/data/stocks.db` | SQLite location |
| `FRESHNESS_MINUTE_SECONDS` / `_HOUR_` / `_MONTH_` | `20` / `3600` / `86400` | per-tier "serve from DB without calling Alpaca" windows |
| `MONTH_BACKFILL_DAYS` | `365` | how far back the first daily-bar fetch reaches; the all-time view then grows from there |
| `BACKFILL_ENABLED` | `1` | run the startup + periodic backfill sweep (`0`/`false` disables) |
| `BACKFILL_INTERVAL_SECONDS` | `600` | seconds between sweeps |
| `BACKFILL_RATE_LIMIT_PAUSE_SECONDS` | `60` | pause before retrying a rate-limited pair once during a sweep |

Retention: minute bars 24 h, hourly bars 30 d, daily bars kept forever.
See `backend/app/config.py` for the full list.

## Frontend behaviour

- **Leaderboard** (`/`) polls `/api/stocks` every 20 s and ranks the board by
  today's change (`change_percent`, highest first; rows without a figure sink
  to the bottom, ties by ticker — `utils/sortStocks.ts`). Every poll re-ranks,
  and `StockTable` animates the moves with a FLIP pass (`utils/flip.ts`):
  slot positions are measured relative to the rows container (so scrolling
  between polls never reads as movement), moved rows are snapped back and
  released to slide into their new rank over `--motion-reorder` (540 ms, a
  slightly springy curve with a ≤96 ms stagger by new rank). Direction is
  made visible: risers get a green tint, a slight lift/shadow and pass over
  the rows they overtake, fallers get a red tint and sit slightly back
  (`is-moved-up` / `is-moved-down`, ~0.9 s), and each moved row shows a
  `▲n` / `▼n` rank-delta chip after its ticker for ~2 s (`RankDeltaChip`).
  `prefers-reduced-motion` drops the slide and lift (instant reorder; the
  tints/chips still appear). jsdom can't do layout, so
  `e2e/leaderboard-reorder.mjs` (Playwright, optional) checks the slide in a
  real browser. Rows are `React.memo`'d; stale/errored
  rows grey out and keep their last price (no error text). A single "lost data
  communication" banner appears if the backend is unreachable.
- **Stock detail** (`/stock/:ticker`) has a **1D / 30D / ALL** range selector
  kept in the URL (`?range=all`, via `useHistoryRange`). The chart polls
  `['stock', ticker, 'history', range]`; polled points are appended/deduped by
  timestamp so the chart never remounts, and switching ticker or range
  replaces the series. **ALL** is the all-time view and lengthens as the
  backend's daily tier accumulates.
- The **Stored data** card below the chart shows the full SQLite contents for
  the stock (`/api/stocks/{ticker}/stored`): a Minute / Hourly / Daily tier
  switch (with row counts; defaults to the tier behind the selected range),
  and every stored column — `ts`, `price`, open/high/low/close, volume, VWAP,
  trade count, `recorded_at` — newest first, plus the tier's last Alpaca fetch
  time and a converted **Close (£)** column.
- **GBP display.** Every price (rows, detail ticker, chart axis/tooltip) is
  shown in GBP. `FxRateProvider` (in `AppProviders`) owns one hourly query to
  `https://api.frankfurter.dev/v1/latest?base=USD&symbols=GBP` (ECB reference
  rate, free, no key; override with `VITE_FX_API_URL`) and shares it via
  context; `utils/currency.ts` converts from each stock's API `currency`
  field. A small note discloses the rate and date ("1 USD = 0.7339 GBP · ECB,
  2026-08-20"). If the rate is unavailable the native (USD) figures show and
  the note says "GBP rate unavailable" — nothing ever goes blank.
- **Poll countdown.** The header bar's brass bottom border is
  `PollCountdownBar`: it drains over the 20 s poll interval (read from the
  React Query cache via `hooks/usePollCountdown.ts`), pulses while a fetch is
  in flight, and refills when fresh data lands. Exposed as a `progressbar`
  with the seconds remaining in `aria-valuetext`.
- **Dashboard** (`/dashboard`) is static placeholder cards only.

---

## Project layout

```
.
├── docker-compose.yml
├── .secrets.example.sh         # template -> copy to .secrets.sh (git-ignored)
├── docs/ptb/uk-stocks-dashboard.md   # original plan (pre-Alpaca, historical)
├── backend/
│   ├── Dockerfile · pyproject.toml   # deps: fastapi, uvicorn, httpx, pydantic
│   ├── app/
│   │   ├── main.py             # FastAPI app, CORS, SQLite init + backfill task on startup
│   │   ├── config.py           # all env-driven settings
│   │   ├── backfill.py         # startup + periodic sweep refreshing stale (tier, ticker) pairs
│   │   ├── tickers.py          # the fixed 20 (ticker, name, sector)
│   │   ├── schemas.py          # pydantic response models
│   │   ├── alpaca_client.py    # all Alpaca HTTP calls (httpx)
│   │   ├── storage.py          # SQLite bars_* tiers + summaries + fetch_log + fetch_claims
│   │   └── routers/stocks.py   # the endpoints + DB-first refresh / lease / backoff logic
│   └── tests/                  # pytest (alpaca_client, storage, router, leaderboard table, backfill)
└── frontend/
    ├── Dockerfile · package.json · vite.config.ts
    ├── tsconfig.json           # solution file -> tsconfig.app.json (src) + tsconfig.node.json (vite.config)
    ├── public/logos/           # {TICKER}.svg + _placeholder.svg + m3-logo.svg
    ├── e2e/leaderboard-reorder.mjs   # optional browser check of the re-rank slide
    └── src/
        ├── main.tsx · router.tsx
        ├── api/                # client.ts, types.ts, queries.ts (TanStack hooks), fx.ts (USD->GBP rate)
        ├── hooks/              # useHistoryRange (?range= <-> HistoryRange), usePollCountdown
        ├── utils/              # currency.ts (GBP conversion), sortStocks.ts, flip.ts
        ├── providers/{AppProviders,FxRateProvider}/
        ├── pages/{LeaderboardPage,StockDetailPage,DashboardPage}/
        └── components/
            ├── layout/{AppShell,PollCountdownBar}
            ├── leaderboard/{StockTable,StockRow}
            ├── stock-detail/{StockHeader,PriceTicker,RangeSelector,StockChart,RawDataTable}
            ├── dashboard/{MarketSummaryCard,SectorBreakdownCard,TopMoversCard}  # placeholders
            └── shared/{CompanyIcon,ChangeIndicator,ErrorBadge,ConnectionBanner,FxRateNote}
```

Frontend convention: every component is a folder with `Component.tsx`,
`Component.props.ts`, `Component.test.tsx`, `Component.css`. Pages are thin
composition only; data lives in hooks/child components.

---

## Status

The Alpaca / SQLite / US-ticker migration is complete as of 2026-08-20:
backend 104/104 pytest, frontend 203/203 vitest, `npm run build` green, and a
live smoke test against Alpaca (IEX feed) confirmed 20/20 fresh snapshots
(incl. `BRK.B`), 1d ≈ 390 minute bars, 30d ≈ 180 hourly bars, all-time ≈ 250
daily bars (growing), 404/422 handling and cache hits. Same day: GBP display,
the stored-data card and the all-time range were added and verified in the
browser.

Remaining nice-to-haves (not blockers):

- The header title uses **Acthirey** (Roomspace Creative Lab, via fontesk.com),
  bundled at `frontend/src/styles/assets/fonts/`. Its bundled readme says the
  demo build is **for personal use only — no commercial use** — fine for this
  local project, but buy the commercial license from roomspacecreativelab.com
  (or swap the font) before any commercial use.
- Company logos in `public/logos/` are simple generated letter marks, not
  official brand assets.
- The dashboard page is still placeholder-only by design.
- The GBP rate is fetched by the browser from a third party (Frankfurter);
  moving it behind the backend (with the same stale-on-failure treatment as
  Alpaca) would be the next hardening step.
- No CI workflow or LICENSE yet.
- `docs/ptb/uk-stocks-dashboard.md` is the original yfinance-era plan, kept for
  history; this README is the current source of truth.
