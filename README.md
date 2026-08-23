# Mario's Money Makers (M³)

Local stock dashboard for a fixed universe of **20 US large caps** (AAPL, MSFT,
NVDA, … NFLX), fed by the **Alpaca Market Data API** (free plan, IEX feed) and
backed by a **SQLite** store so the app keeps serving the last known data when
Alpaca is slow, rate-limited, or down.

- **Backend** — Python 3.11+ / FastAPI. SQLite-first everywhere: the
  leaderboard is one batched snapshot call per ~20 s *per database* into a
  20-row `summaries` table (no in-memory cache; survives restarts; single
  flight across concurrent requests via an in-process lock), exponential
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

## Mock data server (offline development)

`mock/alpaca_mock.py` is a stdlib-only stand-in for the Alpaca data *and*
trading APIs. It implements the three endpoints the backend calls —
`/v2/stocks/snapshots`, `/v2/stocks/{symbol}/bars` (paginated) and the
market clock `/v2/clock` — and serves a deterministic sinusoidal price for
every symbol:

```
price(t) = 100 + 20 · sin(2π · t / 20 min + phase(symbol))
```

i.e. every stock oscillates between 80 and 120 on a 20-minute cycle. Each
of the 20 symbols gets a different phase so the leaderboard keeps
re-ranking (`--no-phase-shift` makes them identical); `--offset`,
`--amplitude`, `--period-minutes` tune the curve. Minute/hour bars are
generated 24/7 (pass `--sessions-only` to emit them only inside the
13:30Z–20:00Z session, every day, so the 1D chart shows its greyed
market-closed block); daily bars are one per weekday stamped 04:00Z like
Alpaca's. The mock clock reports the market open 13:30Z–20:00Z every day
(weekends included, in both modes) so the banner shows both states. No
keys, no network.

The backend needs no code change — it reads `ALPACA_DATA_BASE_URL` and
`ALPACA_TRADING_BASE_URL` (point both at the mock). Always pair it with a
**separate** `STOCKS_DB_PATH` so mock bars are never upserted into your real
`stocks.db`:

```bash
# local, one command (mock on :8500 + backend on :8000, separate mock.db)
./mock/dev.sh                      # then `cd frontend && npm run dev` as usual

# or by hand
python3 mock/alpaca_mock.py --port 8500 &
cd backend && ALPACA_DATA_BASE_URL=http://localhost:8500 \
  ALPACA_TRADING_BASE_URL=http://localhost:8500 \
  STOCKS_DB_PATH=$PWD/data/mock.db uv run uvicorn app.main:app --port 8000

# Docker Compose overlay
docker compose -f docker-compose.yml -f docker-compose.mock.yml up --build
```

`curl localhost:8500/healthz` shows the active curve parameters. Because the
period divides a day exactly, daily open/close are the same every day, so
the **ALL** view is flat by design; **1D** shows the sine.

---

## Tests

```bash
cd backend  && uv run pytest        # 284 tests — Alpaca mocked via httpx.MockTransport, SQLite in tmp dirs
cd frontend && npm run test         # 262 vitest tests — API and FX mocked, no network
cd frontend && npm run build        # tsc -b type-check + vite production build
```

No test touches the network or your real credentials.

---

## Data flow

```
 Browser (React + TanStack Query)         Backend (FastAPI, app/routers/stocks.py)          External
 ────────────────────────────────         ────────────────────────────────────────          ────────
 poll /api/stocks            every 20 s ─►  SQLite fresh? ──yes──► serve table ──────────────► JSON
 poll /api/stocks/{t}/history  "    "          │ no
 poll /api/stocks/{t}/stored   "    "          ▼
 poll /api/market/clock        "    "     (same rule; the `meta` table is its cache)
                                          in-process lock (double-checked freshness)
                                               │ (someone else fetching? serve table as-is)
                                               ▼
                                          fetch ──────────────────────────────────────────► data.alpaca.markets
                                               │  (market clock: GET /v2/clock ──────────────► paper-api.alpaca.markets)
                                          write SQLite (rows + fetch_log stamp) → serve
                                               ▲
 backfill sweep (startup, then every 10 min) ──┘  same refresh paths, no browser required

 hourly USD→GBP rate ───────────────────────────────────────────────────────────────────────► api.frankfurter.dev
```

The rule everywhere is **SQLite first, Alpaca only when stale, never block a
request on someone else's fetch**. There is no in-memory cache in the
backend; the DB tables *are* the cache (see [What the store holds](#what-the-store-holds)).

### 1. Leaderboard request — `GET /api/stocks` (and `/api/stocks/{ticker}`)

`_get_summaries_batch` in `app/routers/stocks.py`:

1. **Fresh?** Read the `fetch_log` stamp `(summaries, *)`. Younger than
   `CACHE_TTL_SECONDS` (20 s, same as the poll) → return the `summaries`
   table, `is_stale=false`, **zero Alpaca calls**. (`change` /
   `change_percent` are derived on read, never stored.)
2. **Backing off?** After a *total* failure (every row errored) an
   in-process exponential backoff (`90 s × 2ⁿ⁻¹`, capped at 600 s) blocks
   refetches → serve the table flagged `is_stale=true`.
3. **In-process single flight.** Take the `summaries` `asyncio.Lock` and
   re-run 1–2. A burst of concurrent polls in one process collapses into
   one fetch: when the leader succeeds the waiters find the stamp fresh
   and serve the new table; when the leader fails they find backoff active
   and serve stale — they do **not** retry.
4. **Fetch.** One `GET /v2/stocks/snapshots` for all 20 symbols, in a
   worker thread (sync `httpx`). `fetch_summaries` never raises: request
   failures come back as an all-error batch, 429 flagged distinctly.
   - **Success** → `upsert_summaries` writes all 20 rows + the stamp in one
     `BEGIN IMMEDIATE` transaction, monotonic on `fetched_at` (a late or
     duplicate writer can never overwrite newer data); each ticker's latest
     minute bar is persisted best-effort into `bars_minute`; backoff is
     reset; **then** the fresh batch is returned. Write first, serve second.
   - **Per-ticker failure** (symbol missing from the response, no
     `prevDailyBar`) → that row is flagged, the rest of the batch is fine;
     never a 5xx.
   - **Total failure** → nothing written, one backoff failure recorded (by
     the leader only), table served stale. Cold start with an empty table
     serves the error-flagged batch so the UI still renders (greyed rows).
   - The `summaries` lock is released in `finally`, so an exception mid-fetch
     can never wedge future requests behind it.

### 2. History request — `GET /api/stocks/{ticker}/history?range=…`

Same shape, per `(ticker, tier)` with `1d → bars_minute`, `30d → bars_hour`,
`all → bars_days` (daily bars, never pruned):

1. `refresh_history` takes the per-pair lock, re-checks the pair's
   `fetch_log` stamp against the tier's freshness window (20 s / 1 h / 24 h),
   then `fetch_bars` in a worker
   thread (`GET /v2/stocks/{symbol}/bars`, paginated via `next_page_token`,
   `adjustment=split`).
2. On success: upsert bars (`PRIMARY KEY (ticker, ts)`, so overlapping
   windows never duplicate), `record_fetch`, prune retention (minute 24 h,
   hour 30 d). An empty bar list (weekend) is a successful fetch and is
   stamped too.
3. The endpoint then **reads back from SQLite** and returns — the read is
   the retention window for minute/hour, unbounded for the daily tier
   (that is what makes `all` grow by one bar per trading day).
4. On `AlpacaError`: serve whatever is stored with `is_stale=true`; **503
   only when the DB holds nothing** for that pair. No backoff here — the
   next caller simply retries.

### 2b. Market clock — `GET /api/market/clock` (`app/routers/market.py`)

The US market open/closed state is not computed anywhere in the app any
more: it is Alpaca's legacy market clock, `GET /v2/clock` on the **trading**
host (`ALPACA_TRADING_BASE_URL`, paper by default) → `{timestamp, is_open,
next_open, next_close}`. The backend stores the last fetched clock in the
`meta` table under `market_clock` (JSON, with its `fetched_at`) and serves
it from there until one of the boundaries it carries arrives: while `now` is
before **both** `next_open` and `next_close` the stored clock still describes
the present, however long ago it was fetched (a clock read on Friday
afternoon is still the right answer on Sunday night). The moment a boundary
passes it is wrong data and is refetched, so a cached "open" is never served
after the close. There is no TTL on top of that: it would refetch clocks that
are still correct — a browser polling every 20 s would mean ~1000 Alpaca calls
a day — to narrow the window on the only thing a boundary check cannot see, a
session the exchange re-schedules *after* we cached it. That is picked up at
the next boundary instead. Otherwise one worker refetches (in-process lock)
and rewrites the key. Alpaca failure → the stored clock with `is_stale: true`
and the error; **503** only when nothing has ever been stored. No backoff:
the next request simply tries again. The backfill sweep refreshes the clock
at the start of every pass too, so `meta.market_clock` is current even with
no browser open. Timestamps are normalised to UTC `…Z` (Alpaca serves New
York offsets).

### 3. Backfill sweep — `app/backfill.py`

Opening a chart is not the only thing that refreshes the bar tables: on
startup, and then every `BACKFILL_INTERVAL_SECONDS` (default 10 min), the
sweep first runs the leaderboard refresh (so a restarted backend has a
populated board before any browser polls) and the market-clock refresh
(§2b), then walks every (tier, ticker)
pair tier-major (minute → hour → daily) and refreshes any whose `fetch_log`
entry has lapsed, through the very same `refresh_history` path (lock +
fetch + upsert + stamp + prune) the endpoint uses — so a request and
the sweep can never double-fetch a pair or disagree about "fresh". Fresh
pairs cost zero Alpaca calls, so steady state is ~20 minute-bar calls per
interval, ~20 hourly per hour and ~20 daily per day (worst case after a
long outage: all 60 in one pass). A rate-limited fetch pauses
`BACKFILL_RATE_LIMIT_PAUSE_SECONDS` and retries that pair once; any other
failure is logged (`app.backfill`) and skipped. Set `BACKFILL_ENABLED=0` to
turn it off.

### 4. In the browser

- `api/queries.ts` — every stock query polls every 20 s
  (`refetchInterval: 20000`, `staleTime: 18000`) under keys `['stocks']`,
  `['stock', ticker]`, `['stock', ticker, 'history', range]`,
  `['stock', ticker, 'stored', tier]`, plus `['market', 'clock']` for the
  market clock (`/api/market/clock`); the header's `PollCountdownBar` reads
  the stock entries to draw the countdown. `QueryClient` defaults:
  `retry: 3`, `refetchOnWindowFocus: false`.
- `api/client.ts` — `VITE_API_BASE_URL` ?? (`''` in prod = same origin via
  Caddy, `http://localhost:8000` in dev).
- `api/fx.ts` / `FxRateProvider` — the **only other external call**: the
  browser fetches the ECB USD→GBP reference rate from Frankfurter once an
  hour and converts every price client-side (`utils/currency.ts`). The
  backend never sees GBP. If the rate is unavailable the native USD figures
  are shown; nothing goes blank.
- Stale/errored rows stay on screen greyed out; only an unreachable backend
  shows the single `ConnectionBanner`.

### What the store holds

| Table | Rows | Role |
| --- | --- | --- |
| `bars_minute` / `bars_hour` / `bars_days` | `(ticker, ts)` PK, `price`, `analytics` JSON (`o/h/l/c/v/vw/n`), `recorded_at` | History tiers behind `1d` / `30d` / `all`. Retention 24 h / 30 d / **never pruned** (the `days` tier holds *daily* bars and backs the all-time view). `recorded_at` is when the row's price was last recorded (unchanged on a same-price refetch). |
| `summaries` | exactly one current-state row per ticker (20, ever) | The leaderboard cache: `price`, `previous_close`, `currency`, `error`, `fetched_at`. |
| `fetch_log` | one row per `(tier, ticker)` + the pseudo-pair `(summaries, *)` | When each pair was last *successfully fetched from Alpaca* — the freshness source of truth (not `recorded_at`, which the leaderboard's minute-bar upserts would keep perpetually fresh). |
| `meta` | key/value | `bars_adjustment` remembers `ALPACA_BARS_ADJUSTMENT` so a change invalidates the bar stamps once; `market_clock` holds the last Alpaca clock (`timestamp`, `is_open`, `next_open`, `next_close`, `fetched_at`) — the cache behind `/api/market/clock`. |

WAL journal mode; timestamps stored as `YYYY-MM-DDTHH:MM:SSZ` text so string
order equals chronological order. Location: `STOCKS_DB_PATH`
(`backend/data/stocks.db` locally, the `stocks-data` volume in Docker).

---

## API

All endpoints are `GET`, JSON, CORS-restricted to `ALLOWED_ORIGINS` (GET
only). Interactive docs at `/docs` (Swagger UI), `/redoc`, schema at
`/openapi.json`.

| Endpoint | Returns | Behaviour / failure modes |
| --- | --- | --- |
| `/api/health` | `{"status": "ok"}` | Liveness probe, no I/O; used by the prod Docker healthcheck (every 30 s). |
| `/api/stocks` | `StocksResponse` — `updated_at` + all 20 `StockSummary` rows (`ticker`, `name`, `sector`, `price`, `previous_close`, `change`, `change_percent`, `currency`, `is_stale`, `error`) | **Always 200.** Served from the `summaries` table while its `fetch_log` stamp is within `CACHE_TTL_SECONDS`; otherwise exactly one worker refetches (in-process lock) and rewrites all 20 rows atomically. Per-ticker failures are flagged, never fatal. Total failure → nothing written, table served `is_stale`, backoff engaged (cold start with an empty table: the error-flagged batch). See [Data flow §1](#1-leaderboard-request--get-apistocks-and-apistocksticker). |
| `/api/stocks/{ticker}` | one `StockSummary` from the same table/batch | Same flow as above. **404** for tickers outside the fixed 20. |
| `/api/stocks/{ticker}/history?range=1d\|30d\|all` | `HistoryResponse` — `ticker`, `interval` (`1m`/`1h`/`1d`), `range`, `points: [{t, close}]`, `is_stale`, `error` | SQLite is the cache: served from the DB while the pair's last successful Alpaca fetch is within the tier's freshness window; otherwise one worker refreshes and the DB is read back. On Alpaca failure returns stored rows with `is_stale: true`; **503** only when the DB has nothing for that ticker+tier. **404** unknown ticker, **422** unknown `range`. `all` is the all-time view (every daily bar ever stored; first fetch backfills `DAY_BACKFILL_DAYS`). See [Data flow §2](#2-history-request--get-apistockstickerhistoryrange). |
| `/api/market/clock` | `MarketClockResponse` — `timestamp`, `is_open`, `next_open`, `next_close` (UTC `…Z`), `fetched_at`, `is_stale`, `error` | Alpaca's `GET /v2/clock`, cached in `meta.market_clock`: served from the DB until one of the boundaries it carries (`next_open`/`next_close`) is reached — no TTL, an old `fetched_at` alone is not a reason to refetch; on a passed boundary one worker refetches (in-process lock). Alpaca failure → stored clock `is_stale: true`; **503** only when nothing was ever stored. See [Data flow §2b](#2b-market-clock--get-apimarketclock-approutersmarketpy). |
| `/api/stocks/{ticker}/stored?tier=minute\|hour\|days` | `StoredDataResponse` — `table`, `currency`, `last_fetch_at`, `counts` per tier, `rows` (every `bars_<tier>` row: `ts`, `price`, parsed `analytics`, `recorded_at`), oldest first | Read-only inspection of the SQLite store: **never calls Alpaca, never writes.** **404** unknown ticker, **422** unknown tier; an empty tier is `200` with `rows: []`. |

Status codes at a glance: `200` (including stale data), `404` unknown
ticker, `422` bad `range`/`tier`, `503` history (or the market clock) with
Alpaca down *and* nothing stored for it. Nothing here returns `500` for
Alpaca reasons.

Alpaca calls behind the API: `fetch_summaries` makes **one**
`GET /v2/stocks/snapshots` request for the whole universe; history uses
`GET /v2/stocks/{symbol}/bars` with pagination; the market clock is
`fetch_clock` → `GET /v2/clock` on the *trading* host
(`ALPACA_TRADING_BASE_URL`, not the data host). Every request is capped at
`ALPACA_TIMEOUT_SECONDS` and carries the `KEY_ID`/`SECRET` headers (the data
requests also `feed=iex`).

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `KEY_ID` / `SECRET` | — | Alpaca credentials (see [Secrets](#secrets)) |
| `ALPACA_DATA_BASE_URL` | `https://data.alpaca.markets` | data API host (snapshots, bars) |
| `ALPACA_TRADING_BASE_URL` | `https://paper-api.alpaca.markets` | trading API host, used only for the market clock (`/v2/clock`); paper by default because the setup above creates paper keys — set `https://api.alpaca.markets` for live keys |
| `ALPACA_FEED` | `iex` | free-plan feed |
| `ALPACA_BARS_ADJUSTMENT` | `split` | `adjustment` sent on every bars request (`raw`\|`split`\|`dividend`\|`all`); `split` keeps history split-adjusted instead of Alpaca's as-traded `raw` default. Changing it invalidates the stored bar fetch stamps once so existing rows are refetched (snapshots unaffected) |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | comma-separated CORS origins |
| `ALPACA_TIMEOUT_SECONDS` | `5.0` | httpx timeout for every Alpaca request |
| `CACHE_TTL_SECONDS` | `20` | leaderboard freshness: serve the `summaries` table without calling Alpaca while its last fetch is younger than this |
| `BACKOFF_BASE_SECONDS` / `BACKOFF_MAX_SECONDS` | `90` / `600` | exponential backoff after a total batch failure |
| `STOCKS_DB_PATH` | `backend/data/stocks.db` | SQLite location |
| `FRESHNESS_MINUTE_SECONDS` / `_HOUR_` / `FRESHNESS_DAY_SECONDS` | `20` / `3600` / `86400` | per-tier "serve from DB without calling Alpaca" windows |
| `DAY_BACKFILL_DAYS` | `365` | how far back the first daily-bar fetch reaches; the all-time view then grows from there |
| `BACKFILL_ENABLED` | `1` | run the startup + periodic backfill sweep (`0`/`false` disables) |
| `BACKFILL_INTERVAL_SECONDS` | `600` | seconds between sweeps |
| `BACKFILL_RATE_LIMIT_PAUSE_SECONDS` | `60` | pause before retrying a rate-limited pair once during a sweep |

Retention: minute bars 24 h, hourly bars 30 d, daily bars kept forever.
See `backend/app/config.py` for the full list.

**Run one backend process per database file.** Single flight is purely
in-process (an `asyncio.Lock` plus double-checked freshness), so
`uvicorn --workers N` or two containers pointed at the same
`STOCKS_DB_PATH`/volume would let requests in different processes duplicate
Alpaca fetches — harmless to the data (the write paths are conflict-tolerant
independently of any lock) but wasteful of API quota. SQLite already pinned
this app to one host; this pins it to one process on that host too.

## Frontend behaviour

### Pages & routes

`router.tsx` — every route renders inside the `AppShell` layout (header
with the M³ mark, nav links and the `PollCountdownBar`; `<Outlet/>` below).
Pages are thin composition only; data lives in hooks and child components.

| Route | Page | Data | What it shows |
| --- | --- | --- | --- |
| `/` | `pages/LeaderboardPage` | `useStocksQuery()` → `/api/stocks`; `MarketStatusBanner` → `useMarketClockQuery()` → `/api/market/clock` | `MarketStatusBanner` (open/closed from the backend market clock + a local countdown to `next_open`/`next_close`; hidden until the clock has loaded), `ConnectionBanner` if the API is unreachable, the `FxRateNote` rate disclosure, and `StockTable` — 20 `StockRow`s ranked by `change_percent` (`utils/sortStocks.ts`) with FLIP re-rank animation, rank-delta chips, GBP prices via `ChangeIndicator`/`CompanyIcon`. |
| `/stock/:ticker` | `pages/StockDetailPage` | `useStockDetailQuery(ticker)` → `/api/stocks/{ticker}`; `useStockHistoryQuery(ticker, range)` → `/history?range=`; `useStoredDataQuery(ticker, tier)` → `/stored?tier=` | `StockHeader` + `PriceTicker`, `RangeSelector` (1D / 30D / ALL, kept in `?range=` by `hooks/useHistoryRange`), `StockChart` (Recharts), and the `RawDataTable` "Stored data" card showing the SQLite rows for the Minute / Hourly / Daily tier, plus the `FxRateNote` rate disclosure. A query error (backend down, or the API's 404 for an unknown ticker) shows the `ConnectionBanner`. |
| `/dashboard` | `pages/DashboardPage` | none (static) | Placeholder cards only: `MarketSummaryCard`, `SectorBreakdownCard`, `TopMoversCard`. |

### Behaviour

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
│   │   ├── storage.py          # SQLite bars_* tiers + summaries + fetch_log + meta
│   │   ├── routers/stocks.py   # the stock endpoints + DB-first refresh / backoff logic
│   │   └── routers/market.py   # /api/market/clock: Alpaca /v2/clock cached in meta
│   └── tests/                  # pytest (alpaca_client, storage, routers, leaderboard table, backfill)
└── frontend/
    ├── Dockerfile · package.json · vite.config.ts
    ├── tsconfig.json           # solution file -> tsconfig.app.json (src) + tsconfig.node.json (vite.config)
    ├── public/logos/           # {TICKER}.svg + _placeholder.svg + m3-logo.svg
    ├── e2e/leaderboard-reorder.mjs   # optional browser check of the re-rank slide
    └── src/
        ├── main.tsx · router.tsx
        ├── api/                # client.ts, types.ts, queries.ts (TanStack hooks), fx.ts (USD->GBP rate)
        ├── hooks/              # useHistoryRange (?range= <-> HistoryRange), usePollCountdown
        ├── utils/              # currency.ts (GBP conversion), sortStocks.ts, flip.ts, marketHours.ts (clock/countdown formatting), intradaySeries.ts (1D market-closed blocks)
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
