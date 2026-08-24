# Mario's Money Makers (M³)

This is my submission for the Oakland Engineering task. In this repository you will find a full stack market-tracking dashboard for 20 US large caps, fed by Alpaca's API with SQLite as a durable cache. Deployed at <https://mariosmoneymakers.duckdns.org>.

<p>
   <img width="1268" height="770" alt="image" src="https://github.com/user-attachments/assets/cc403de7-5e66-4c9e-a8f9-3a44e2b5b7eb" />
    <em>The general architectural overview of how the application is currently deployed.</em>
</p>

This application is made up of 3 main parts: a **TypeScript React** frontend, a **Python FastAPI** backend, and an **SQLite** database acting as a cache (both to serve market data quickly and to keep Alpaca calls down).

Every read path is the same three steps, whichever endpoint you hit:

1. **Is the stamp fresh?** (`fetch_log` for stocks and history, a boundary check for the clock.) If yes → serve straight from SQLite, zero Alpaca calls.
2. **If not**, take the in-process `asyncio.Lock` and re-check — the worker you queued behind may have just refreshed, in which case there is nothing left to do. That double-checked freshness is the whole single-flight mechanism.
3. **Fetch, write, then serve.** The response is read back from the DB after the write, so the browser sees exactly what was persisted.

The backfill sweep (startup, then every 10 minutes) walks the same code paths with no browser involved, so a cold box populates itself, and a request and the sweep can never double-fetch a pair or disagree about what "fresh" means.

> *Note*: The back fill after the cold start is done mostly to keep the 24h and 30d stock history fresh, as these aim to keep granular data for a shorter time frame. This is done to minimize storage while keeping a granular view of the past day/month. 


## Running it locally

```bash
git clone https://github.com/MarioArchives/Marios-Money-Makers.git
cd Marios-Money-Makers
```

### Option A — Docker Compose (one command)

Needs Docker with the Compose v2 plugin, and Alpaca **paper** API keys from
<https://app.alpaca.markets>.

```bash
cp .secrets.example.sh .secrets.sh   # then edit KEY_ID / SECRET
source .secrets.sh
docker compose up --build
```

Frontend on <http://localhost:5173>, backend on <http://localhost:8000> (Swagger
UI at `/docs`). Both services bind-mount their source for hot reload, and the
SQLite file lives in the named volume `stocks-data`, so it survives rebuilds.
Without keys the app still starts — every Alpaca call just fails and the board
renders greyed rows.

### Option B — Two terminals (no Docker)

Needs Python ≥ 3.11 with [`uv`](https://docs.astral.sh/uv/) and Node 22.

```bash
# terminal 1 — backend
source .secrets.sh
cd backend && uv sync
uv run uvicorn app.main:app --reload --port 8000

# terminal 2 — frontend
cd frontend && npm install
npm run dev            # -> http://localhost:5173
```

The SQLite file is created at `backend/data/stocks.db` (git-ignored). The frontend
reads `VITE_API_BASE_URL` (defaults to `http://localhost:8000`; set it in
`frontend/.env.local` if you move the backend port).

### Option C — Offline, against the mock (no keys, no network)

`mock/alpaca_mock.py` is a stdlib-only stand-in for the three Alpaca endpoints the
backend calls (`/v2/stocks/snapshots`, `/v2/stocks/{symbol}/bars`, `/v2/clock`). It
serves a deterministic sine wave — every symbol oscillates between 80 and 120 on a
20-minute cycle, each with a different phase so the leaderboard keeps re-ranking.

```bash
./mock/dev.sh                     # mock on :8500 + backend on :8000, Ctrl-C stops both
cd frontend && npm run dev        # in another terminal

# or as a Compose overlay
docker compose -f docker-compose.yml -f docker-compose.mock.yml up --build
```

The script points both `ALPACA_DATA_BASE_URL` and `ALPACA_TRADING_BASE_URL` at the
mock and uses a **separate** database (`backend/data/mock.db`), so synthetic bars
are never mixed into real Alpaca data. `curl localhost:8500/healthz` shows the
active curve parameters. Two flags matter for testing the closed-market UI:
`--sessions-only` emits bars only inside the session window, so the 1D chart shows
its greyed "market closed" block, and the mock clock reports the market open
13:30Z–20:00Z _every_ day, so both banner states are reachable whenever you happen
to be working.

### Tests

```bash
cd backend  && uv run pytest     # Alpaca mocked, SQLite in tmp dirs
cd backend  && uv run ruff check app tests
cd frontend && npm run test      # API and FX mocked
cd frontend && npm run build     # tsc -b type-check + production build
```

No test touches the network or real credentials. What each file covers is in
[The test suite](#the-test-suite).

## Endpoints

| URL | What it tells you |
| --- | --- |
| `/api/health` | FastAPI process is alive (no I/O — says ok even if Alpaca/DB are broken) |
| `/api/stocks` | Leaderboard batch; check `is_stale` / `error` per row and `updated_at` |
| `/api/stocks/{ticker}` | One ticker's summary |
| `/api/stocks/{ticker}/history?range=1d\|30d\|all` | What the chart gets; `is_stale`, `error`, `points[]` |
| `/api/stocks/{ticker}/stored?tier=minute\|hour\|days` | Raw SQLite contents + `last_fetch_at` + row counts |
| `/api/market/clock` | The next market open or close |

FastAPI's Swagger UI is served at `/docs` on the backend, but Caddy only forwards
`/api/*`, so it 404s through the domain.

## SQLite tables

The database is a cache rather than a system of record — Alpaca is always the
source of truth, and there is no in-memory layer anywhere in the backend, so a
restart loses nothing. The file defaults to `STOCKS_DB_PATH`
(`backend/data/stocks.db` locally, the `stocks-data` Docker volume in production).
Six tables:

| Table | Holds |
| --- | --- |
| `bars_minute` / `bars_hour` / `bars_days` | historical bars at three granularities |
| `fetch_log` | when each `(tier, ticker)` pair was last successfully fetched |
| `summaries` | the leaderboard's current state |
| `meta` | key/value bookkeeping — bar adjustment mode, cached market clock |

### `bars_minute` / `bars_hour` / `bars_days` — granularity/longevity tiers

Identical schema, one per retention tier. They back the **1D**, **30D** and **ALL**
ranges on the stock detail page.

```sql
CREATE TABLE IF NOT EXISTS bars_<tier> (
    ticker      TEXT NOT NULL,   -- ticker symbol
    price       REAL NOT NULL,   -- bar close
    analytics   TEXT NOT NULL,   -- JSON: {"o","h","l","c","v","vw","n"}
    ts          TEXT NOT NULL,   -- bar time, ISO-8601 UTC
    recorded_at TEXT NOT NULL,   -- when this row's price last changed
    PRIMARY KEY (ticker, ts)
);
```

| Table | Alpaca timeframe | Serves | Retention |
| --- | --- | --- | --- |
| `bars_minute` | `1Min` | `range=1d` | 24 hours |
| `bars_hour` | `1Hour` | `range=30d` | 30 days |
| `bars_days` | `1Day` | `range=all` | never pruned — one row per trading day |

The composite primary key does double duty: it is the upsert target, so re-fetching
an overlapping window can never duplicate a row, and it covers the `(ticker, ts)`
range scan every read does. At this scale (20 tickers, a few thousand rows) no other
index earns its keep.

`recorded_at` means _when this row's price was last recorded_, not _when we last
fetched it_. `upsert_bars` always refreshes `price` and `analytics` (a live bar's
volume keeps climbing even when its close hasn't moved) but only moves
`recorded_at` when the incoming price actually differs:

```sql
recorded_at = CASE WHEN bars_minute.price IS excluded.price
              THEN bars_minute.recorded_at ELSE excluded.recorded_at END
```

So the leaderboard's ~20 s minute-bar re-upsert of an unchanged close leaves the
stamp alone, while a real tick — or a split re-adjusting historical bars — restamps
exactly the rows whose price changed. That is what makes the "Stored data" table on
the detail page honest about when a price last moved.

### `fetch_log` — freshness

```sql
CREATE TABLE IF NOT EXISTS fetch_log (
    tier       TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tier, ticker)
);
```

One row per `(tier, ticker)`, recording when that pair was last **successfully
fetched from Alpaca**. This is the source of truth for "is the cache fresh?", and it
is deliberately not derived from the bar rows: `recorded_at` would be wrong for the
minute tier, since an actively traded ticker gets its stamp moved every poll, so the
tier would look permanently fresh and the intraday backfill would never run. A
legitimately empty fetch (a weekend, a thin IEX symbol) also stores no bars but is
still a fetch, and belongs here.

The leaderboard's snapshot fetch is stamped in the same table under the pseudo-pair
`(tier='summaries', ticker='*')`, so one freshness mechanism covers everything.

### `summaries` — the leaderboard's current state

```sql
CREATE TABLE IF NOT EXISTS summaries (
    ticker         TEXT PRIMARY KEY,
    price          REAL,            -- NULL on an error row
    previous_close REAL,            -- NULL when Alpaca had no prevDailyBar
    currency       TEXT NOT NULL,
    error          TEXT,            -- per-ticker error, NULL when good
    fetched_at     TEXT NOT NULL    -- batch stamp
);
```

Exactly 20 rows, ever — current state, no history, nothing to prune. `change` and
`change_percent` are derived on read rather than stored, so they can never drift out
of step with `price`/`previous_close`.

`upsert_summaries` writes all 20 rows _and_ the `fetch_log` stamp inside one
`BEGIN IMMEDIATE` transaction that rolls back on any exception, so a reader never
sees a half-written batch or a stamp without its rows. Both upserts are monotonic —
`... WHERE excluded.fetched_at > summaries.fetched_at` — so a slow writer holding an
older batch than what is already stored is silently ignored, and re-writing the same
batch is a no-op.

### `meta` — small key/value bookkeeping

```sql
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

Two keys today:

- **`bars_adjustment`** — which Alpaca `adjustment` mode (`raw`/`split`/…) the
  stored bars were fetched with. On startup `ensure_bars_adjustment` compares it
  with the configured mode and, if it changed, deletes the three bar tiers'
  `fetch_log` stamps (not the rows) so every tier is stale and gets refetched and
  overwritten in place. This runs once per change, not on every boot.
- **`market_clock`** — the latest Alpaca market clock as one JSON blob (`timestamp`,
  `is_open`, `next_open`, `next_close`, `fetched_at`), the cache behind
  `/api/market/clock`. It expires on a boundary crossing rather
  than a TTL: while `now` is before **both** `next_open` and `next_close`, the
  stored clock still describes the present no matter how old it is (a clock read on
  Friday afternoon is still the right answer on Sunday night).
## Error handling

The guiding rule is that **stale data beats no data**. A user watching the board
during an Alpaca outage should see the last known prices greying out, not an error
page. Every response carries `is_stale` and `error` so the browser can show *how*
degraded the data is, and the layers below are arranged so that a failure in one of
them cannot take out the ones above it.

| What fails | Backend response | What the user sees |
| --- | --- | --- |
| Alpaca slow, down or rate-limiting | `200` with stored rows, `is_stale: true` | Prices grey out, last values stay on screen |
| Alpaca returns junk for one ticker | `200`, that row flagged | One greyed row, the other 19 normal |
| Alpaca down *and* nothing ever stored | `503` (history / clock only) | Empty chart or hidden market banner |
| Unknown ticker or bad `range`/`tier` | `404` / `422` | Connection banner on the detail page |
| Backend process down | nothing (Caddy `502`) | Single "lost data communication" banner, last data retained |
| FX rate provider down | not involved | Native USD prices, "GBP rate unavailable" note |

### Alpaca — the upstream API

Every Alpaca call is isolated in `alpaca_client.py`, so it is the only module that
has to know what "Alpaca is broken" looks like. Four failure kinds:

- **Transport failures** — connection refused, DNS failure, TLS error, or exceeding
  `ALPACA_TIMEOUT_SECONDS` (5 s). Every `httpx.HTTPError` becomes an `AlpacaError`;
  no raw httpx exception ever escapes the module. The timeout also bounds how long a
  hung request can hold the lock for that pair.
- **Rate limiting (429)** — flagged distinctly, because callers react to it
  differently: the leaderboard engages backoff, and the backfill sweep pauses
  `BACKFILL_RATE_LIMIT_PAUSE_SECONDS` before retrying that one pair.
- **Other non-2xx** — bad credentials (401/403), a symbol Alpaca rejects, or an
  upstream 5xx. All become a plain `AlpacaError` carrying the status.
- **Malformed 200s** — a response that *claims* success but does not carry what it
  should. Covered below.

The two fetch contracts differ on purpose. `fetch_summaries` **never raises** — a
request-level failure produces an all-error batch, every requested symbol present
and flagged, so the leaderboard always has 20 rows to render even on a cold start; a
single bad symbol inside an otherwise good response degrades only that symbol.
`fetch_bars` and `fetch_clock` **do raise**, and they fail atomically: no partial bar
lists, even when the malformed bar arrives on page three of a paginated fetch.

### Validating what Alpaca sends back

Every 200 is validated against the documented shape *before* any field is trusted, and
anything that fails is treated exactly like a network failure, with the store left
untouched.

- **The envelope.** The body must parse as JSON *and* be an object. An HTML error page
  from a proxy, a bare array, or a JSON scalar is rejected rather than indexed into.
- **Bar rows.** Each bar's `t` must be a string the timestamp normaliser can parse, and
  `o`/`h`/`l`/`c`/`v`/`vw`/`n` must all be present and genuinely numeric. Booleans do
  not count as numbers, numeric *strings* are rejected rather than coerced, and
  non-finite floats (`NaN`, `Infinity`) are rejected — each would otherwise land in the
  store as something that only breaks downstream.
- **Response identity.** A top-level `symbol` that does not match the symbol requested
  is rejected, so a mixed-up response can never be written under the wrong ticker.
- **Pagination.** `bars` and `next_page_token` must be of the expected type, and the
  token is validated *before* it is followed — a malformed token cannot send the
  paginator into an endless loop.
- **The market clock.** `is_open` must be a real boolean (the string `"true"` or the
  number `1` is rejected), and `timestamp`, `next_open` and `next_close` must all be
  parseable strings.
- **Snapshots** degrade per symbol instead of failing the batch: a wrong-typed value on
  a price path (`latestTrade.p`, `minuteBar.c`, `dailyBar.c`, `prevDailyBar.c`) turns
  that one symbol into an error entry, while a *missing* or null field simply falls
  through to the next fallback in the chain. A malformed `minuteBar` on an otherwise
  good snapshot keeps the summary and only skips the persistence step, logging a
  warning — the leaderboard should not degrade over a field it does not display.

Unknown extra keys are always ignored, so Alpaca adding a field never breaks us.
Timestamps are normalised to exactly `YYYY-MM-DDTHH:MM:SSZ`, converting real offsets to
UTC rather than relabelling them — the market clock serves New York offsets, and getting
this wrong would have shifted every boundary by four hours. None of these validation
failures is ever mistaken for rate limiting, so a malformed body cannot accidentally
trigger backoff.

### The backend itself

- **Reads never raise.** A missing database file, a missing table or a corrupt stored value returns
  an empty result or `None` rather than propagating a `sqlite3` error. That makes a corrupt row
  self-healing: `get_market_clock()` treats malformed JSON or a wrong-typed field as "nothing
  stored", so it is refetched and overwritten.
- **Writes are all-or-nothing**, and nothing is written at all on a failed fetch — which is what
  keeps the pair correctly marked stale for the next caller.
- **A crashed or hung worker cannot wedge the system.** The per-pair `asyncio.Lock` is released in
  a `finally`, so an unexpected exception mid-fetch frees it for the next caller. There is no lease
  to expire and no separate crash-recovery path: a worker that dies mid-fetch is, from the store's
  point of view, just another failed fetch.
- **The background sweep is failure-tolerant by construction.** An exception on one
  `(tier, ticker)` pair is logged and the sweep moves on; a clock-refresh failure is logged at
  WARNING and swallowed so it cannot stop the bar-tier pass; `CancelledError` is re-raised rather
  than absorbed, so shutdown still works.
- **Restarts cost nothing.** The only process-local state is the backoff counters and the locks,
  cheap to rebuild. `init_db` is idempotent and called defensively before writes, so even a wiped
  volume heals itself on the next request.
- **Liveness stays honest.** `/api/health` does no I/O, so it reports the process alive even when
  Alpaca and the database are both broken. That is what the Docker healthcheck polls, and a probe
  that failed because a third party is down would restart a healthy container.

A genuinely unexpected exception — a bug in our own code — still produces a FastAPI `500`. Nothing
converts an *Alpaca* problem into a 500; a 500 means the backend itself is wrong. The gap worth
naming: no global exception handler shapes those into the same `{is_stale, error}` envelope, so an
unhandled bug surfaces as FastAPI's default error body and the frontend treats it as a generic
query failure.

### The frontend

- **Query failures.** TanStack Query retries three times before surfacing an error, and previously
  fetched data stays in the cache throughout — a failed poll never blanks a populated screen.
- **Row-level degradation.** A stale or errored row greys out and keeps its last price. Error
  strings are never rendered on a row; a broken ticker reads as dimming, not as a stack trace in
  the middle of the board.
- **Absent data renders as absent.** The market-status banner returns nothing until the clock has
  loaded, rather than guessing "closed" and flipping a second later. Chart points with unparseable
  timestamps are dropped rather than plotted at epoch zero.
- **The FX call fails independently.** A separate provider on a different host, so when it fails
  prices fall back to native USD with a "GBP rate unavailable" note. Nothing goes blank and no
  other query is affected.
- **An unreachable backend is one banner, not twenty row errors.** Container down, box rebooting,
  network cut or a Caddy `502` all surface as exactly one page-level `ConnectionBanner` ("we have
  lost data communication with the backend"), with the last fetched data left on screen behind it;
  CORS and origin mismatches land in the same bucket. Recovery needs no user action — the polls
  keep running and the first success clears the banner, which is why there is no reload button.
- **The poll countdown doubles as a liveness indicator.** The brass bar in the header drains over
  the 20 s poll, pulses while a fetch is in flight and refills when fresh data lands, so a stalled
  connection is visible before the user has read the banner.

Two honest gaps: there is no React error boundary around the tree, so an exception thrown during
render would blank the page rather than degrade it; and the detail page cannot distinguish the
API's `404` for an unknown ticker from the backend being unreachable — both render the same
connection banner.

### Status codes at a glance

`200` for everything that has data to show, including stale data. `404` for an unknown
ticker, `422` for a bad `range` or `tier`, and `503` only when Alpaca failed *and* the
database holds nothing at all for that resource — the one case where there is genuinely
nothing to render.

## Limitations

- **SQLite is single-process.** While some work has already been done to allow for multi instance writes, this could be improved. In order to allow for this, a change of DB would be needed.
- **One box, no redundancy.** A single EC2 instance with no load balancer and no standby: a reboot
  is a brief outage, an instance loss a rebuild.
- **The universe is fixed at 20 hard-coded US large caps** (`tickers.py`). Adding a stock is a code
  change and a redeploy — no admin UI, no per-user watchlist.
- **Free-plan data.** Alpaca's free tier is the IEX feed, a fraction of total US volume, so prices
  can differ slightly from a consolidated (SIP) feed and thinly traded symbols have gappier
  intraday bars.
- **No authentication, no per-client rate limiting.** The API is public and read-only — survivable
  because it is DB-first, so an abusive client mostly hits SQLite rather than our Alpaca quota, but
  nothing stops someone hammering it.
- **GBP conversion happens in the browser**, using a third-party ECB rate (Frankfurter) fetched
  hourly by the client. The backend never sees GBP, and that call is the frontend's only external
  dependency of its own.
- **A re-scheduled session is picked up late**, since the cached clock is only invalidated by a
  boundary crossing.
- **A ≥30-minute in-session data hole looks like a closure** on the 1D chart, which greys any gap
  of half an hour or more and labels it "market closed". Since dropping the hardcoded calendar it
  cannot distinguish a real session break from an outage; fetching Alpaca's `/v2/calendar` would
  fix it.
- **The dashboard page is placeholder cards only**, by design.
- **No CI.** Tests are green locally and run by hand; nothing enforces that on a push.
- **Company logos are generated letter marks**, not official brand assets, and the display font is
  a personal-use-only demo build — both would need replacing or licensing before commercial use.

## Tradeoffs

- **SQLite as the only cache, rather than Redis or an in-process TTL cache.** The dataset is ~20
  rows of current state plus a few thousand bars and has to survive restarts anyway; one file gives
  persistence and the "Stored data" inspection view for free, with no extra service to run or fail.
  Cost: the single-process ceiling above.
- **Data Granularity.** Decided to use minutes/hours tables as sliding windows for where the most
  granular data could be kept. This is in order to balance a granularity in data and the size of the
  data base. Main concern was to minimize costs of running in AWS. 
- **The database _is_ the cache — no in-memory layer.** Restarts cost nothing and two readers can
  never disagree about freshness. Cost: a disk read per request, microseconds at this size.
- **Boundary-based clock expiry instead of a TTL.** Two Alpaca clock calls a day instead of ~1000,
  with the open/close flip still landing on the exact second. Cost: the late-rescheduling window
  above — rare, and recoverable at the next boundary.
- **Serve stale rather than fail.** Five-minute-old prices during an outage beat an error page.
  Cost: the user must read the visual cue — mitigated by the poll countdown and per-row greying.
- **A fixed 20-ticker universe.** Keeps the leaderboard one batched snapshot call per poll,
  comfortably inside the free tier, and makes data volume predictable. Cost: a code change to add a
  stock.
- **Bugs** There are a couple of ui bugs which could be improved on were more time dedicated to this project.
  This involves the refreshing of the Leader table to sometimes cause movements even when the data is not changing,
  constant querying to the Back-End for newer data even when the market is closed, etc. 

## Deployment

Production is one small EC2 instance (Amazon Linux 2023, t3.micro with 2 GB swap) running
two containers under Compose. Ensure that a correctly formatted .env file is present (see .env.example). 

Deploying is a pull and a rebuild on the box:

```bash
cd /opt/Marios-Money-Makers
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
```

Rolling back is the same command after `git checkout <previous-sha>`. The backend image runs
as a non-root user with locked runtime-only dependencies, and carries a `/api/health`
healthcheck that Compose polls every 30 s. There is nothing to back up: SQLite here is a
re-fetchable cache, and the backfill sweep repopulates it within a minute or two of a cold
start.

## The test suite

Nothing in either suite touches the network or a real credential: Alpaca is faked, SQLite
runs in throwaway directories, and the browser suite mocks the API module.

The backend files are organised by *layer*, not by feature, and each one fakes the world at
a different depth. That is the thing to understand before adding a test — putting it in the
wrong file usually means mocking the wrong thing.

| File | The layer it exercises | Where the fake sits |
| --- | --- | --- |
| `test_alpaca_client.py` | The HTTP boundary | `httpx.MockTransport` — real client code, fake socket |
| `test_storage.py` | The SQLite store | nothing is mocked; a real DB in a tmp dir |
| `test_stocks_router.py` | Endpoint contracts | `fetch_summaries`/`fetch_bars` patched at the router's call site |
| `test_leaderboard_table.py` | DB-first behaviour end to end | counting `MockTransport` — assertions land on the HTTP boundary |
| `test_market_router.py` | The market clock endpoint | `fetch_clock` patched at the router's call site |
| `test_backfill.py` | The background sweep | `fetch_bars` patched; sweep driven directly |
| `test_health.py` | Liveness | nothing to fake |

**`test_alpaca_client.py` — does the app survive what Alpaca actually sends?** Drives the
real client over a mock transport, so request shape and response handling are both covered:
the correct host per endpoint (data vs trading), the `APCA-API-*` headers, `feed=iex`,
`adjustment=split`, `start` but no `end`; the error taxonomy; every validation rule above;
and the two contracts pinned apart (`fetch_summaries` degrades per symbol, `fetch_bars` and
`fetch_clock` raise atomically).

**`test_storage.py` — does the store keep its promises?** No mocking at all: the real
persistence layer against a real SQLite file. Idempotent `init_db`, the `(ticker, ts)` upsert,
the `recorded_at` gating rule, `prune` boundary behaviour (a row exactly at the cutoff
survives), `fetch_log` stamping and tier validation, the monotonic `summaries` batch, the
`meta` helpers including the clock round-trip and its corrupt-value-returns-`None` contract,
and the schema migrations.

**`test_stocks_router.py` — does each endpoint honour its contract?** Alpaca is patched at the
router's call site, so these are about HTTP surface rather than client behaviour: response
shapes, `404`/`422`/`503`, per-tier freshness windows, the derived `change`/`change_percent`,
and the "no straggler" rule where the daily tier's fetch reaches back to the oldest stored bar.

**`test_leaderboard_table.py` — does the DB-first design hold under pressure?** The most
valuable file, and the one that would catch a regression in this app's central claim. It drives
the *real* client over a transport that counts every request, so "exactly one Alpaca call" is
asserted where it matters: a concurrent burst collapsing into one fetch, waiters not retrying
after a leader fails, exponential backoff, a late writer unable to overwrite newer data, atomic
row+stamp writes, a simulated process restart serving from the DB, and the sweep repopulating a
cold board.

**`test_market_router.py` — is the clock cached on the right rule?** Boundary expiry with no
TTL: a stored clock is served with zero Alpaca calls however old its `fetched_at` is, and
refetched the moment `next_open`/`next_close` passes. Also the failure policy, `refresh_clock`
swallowing Alpaca errors for the sweep, and app wiring.

**`test_backfill.py` — does the sweep behave when nobody is watching?** That it refreshes lapsed
pairs and skips fresh ones, pauses and retries once on a rate limit, logs and moves on from other
failures, shares the refresh path with the endpoints, and honours cancellation at shutdown.

**Shared conventions.** Wall clocks are frozen and advanced by hand (`fake_utcnow` for freshness,
`fake_clock` for backoff windows) so nothing sleeps and no test is time-of-day dependent.
`config.DB_PATH` is monkeypatched per test, giving every test its own database. Router module
state (locks, backoff) is reset between tests, and resetting it mid-test is how a "process
restart" is simulated. The autouse fixture in `conftest.py` disables the backfill sweep, so
entering the app's lifespan in a test never kicks off a real one — `test_backfill.py` re-enables
it where the sweep is the subject.

### Frontend — located with the code

Every component owns a `Component.test.tsx` next to its `.tsx`, `.props.ts` and `.css`.
Components never talk to the network in a test: `vi.mock` replaces the `api/queries` module, so a
test states the query result it wants and asserts what renders. There is no MSW layer and no test
server.

| Area | What is being checked |
| --- | --- |
| `components/` | Rendering, interaction, degraded states (stale dimming, error badges, banners), a11y roles/labels |
| `utils/` | Pure logic in isolation — ranking, GBP conversion, countdown formatting, intraday gap detection, FLIP measurement |
| `pages/` | Composition only: that the right children are present and wired, since pages hold no logic |
| `api/` | Query keys, poll intervals and `staleTime`, request URLs, and the FX rate hook |
| `providers/` | Context wiring (query client, FX rate sharing) |
| `hooks/` | `useHistoryRange` URL sync and the poll countdown |

Fake timers cover anything that ticks — the 1 s market-status countdown, the 20 s poll bar — so a
test asserting a countdown never waits for one. `npm run build` runs `tsc -b` first, so type
errors fail the build even though vitest would not catch them.

## Known gaps and next steps

Deployment, roughly in the order I would do them:

1. **CI on every push** — `pytest`, `vitest` and `tsc -b` in GitHub Actions, with a green run a
   precondition for deploying. Today the tests are only as reliable as my remembering to run them.
2. **Build images in CI, not on the production box.** A deploy runs `npm ci` + a Vite build on a
   1 GB instance — hence the Dockerfile's Node heap cap and the 2 GB swap — so a build failure
   happens *in production*, mid-deploy. Pushing tagged images to a registry (ECR/GHCR) makes
   deployment "pull this digest and restart", rollback a tag change, and removes the toolchain
   from the server.
3. **Zero-downtime restarts.** `up -d --build` stops the old container before the new one is
   healthy; starting the replacement, waiting on `/api/health`, then cutting Caddy over removes
   the gap.
5. **Real observability.** Diagnosis is `docker compose logs` today. Structured JSON logs, an
   uptime check on `/api/health` and a few counters (Alpaca calls, 429s, cache-hit ratio, sweep
   duration) would turn "the board looks stale" into an answerable question. The app already logs
   the right events; nothing collects them.
7. **A second instance (and therefore Postgres).** Only worth it if scaling is needed:
   SQLite is what pins the app to one host, so this is a database migration first and a load
   balancer second.

Testing — the suites cover unit behaviour and per-endpoint contracts well; the gaps are the seams
between the pieces:

- **End-to-end browser tests.** No Playwright/Cypress layer at all. Real-browser coverage of the
  core journeys — board loads and re-ranks, detail page switches range and keeps the chart mounted,
  the banner appears when the API dies — would catch what jsdom structurally cannot: anything
  involving layout or animation.
- **A recorded-fixture test against real Alpaca payloads.** One saved response per endpoint,
  replayed through the client, catches the day Alpaca renames a field — something a hand-written
  mock never notices.
- **Real concurrency tests.** Single flight is tested with sequenced calls, not real concurrency;
  firing a burst at one running backend would test the lock as designed rather than as modelled.
  Two processes against one DB file is a different test now — a stress test of the write paths that
  were always meant to tolerate it, asserting that the data converges (no `database is locked`,
  stored rows matching whichever fetch was newest) rather than "exactly one Alpaca call".
- **Property-based tests for the freshness and boundary rules.** `_is_fresh`, the tier windows and
  the backoff schedule are pure functions over timestamps — ideal for Hypothesis to explore the
  ordering edges (equal timestamps, boundaries crossed on the second, clock skew).
- **Accessibility assertions.** Roles and `aria-label`s are checked ad hoc; an `axe` pass over each
  rendered page would cover the rest, including contrast on the stale/greyed states.
