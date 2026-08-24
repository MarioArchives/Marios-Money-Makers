# Mario's Money Makers (M³)

This application is currently deployed in an AWS instance and can be accessed via this domain: https://mariosmoneymakers.duckdns.org

## Overview

This Repo contains a full stack application for a market tracking software. The application restricts itself to 20 stocks, the data of which is retrieved using Alpaca's API. See image below:


<p>
   <img width="1268" height="770" alt="image" src="https://github.com/user-attachments/assets/cc403de7-5e66-4c9e-a8f9-3a44e2b5b7eb" />
    <em>This is the general architectural overview of how the application is currently deployed.</em>
</p>
Here the application is structured in three main parts:

- The Front-End in TypeScript React.
- The back end in Python and FastAPI.
- An SQLite database acting cache to quickly access market data, as well as to limit API calls to Alpaca.

A general pattern is used throughout this application. The Front-End requests some data from the back end, this in turn checks the SQLite instance of the data and checks if it is up to date. If it is, the Back-End returns the data stored in the DB. If the data is not fresh, then the Back-End calls Alpaca for a fresh set of data, updates the data base and serves the Front-End with the new data. This will go in further detail in the SQLite tables section.



## Endpoints

These are the endpoints which the front end uses to talk to the back end.

| URL                                                          | What it tells you                                                                                                     |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `/api/health`                                                | FastAPI process is alive (no I/O — says ok even if Alpaca/DB are broken)                                              |
| `/api/stocks`                                                | Leaderboard batch; check `is_stale` / `error` per row and `updated_at`                                                |
| `/api/stocks/{stock-ticker}`                                 | One ticker's summary                                                                                                  |
| `/api/stocks/{stock-ticker}/history?range=1d\|30d\|all`      | What the chart gets; `is_stale`, `error`, `points[]`                                                                  |
| `/api/stocks/{stock-ticker}/stored?tier=minute\|hour\|days`  | Raw SQLite contents + `last_fetch_at` + row counts                                                                    |
| `/api/market/clock`                                          | This endpoint is used to return the next close or open of the market.                                                 |
| `/docs` → not proxied                                        | FastAPI's Swagger UI lives at `/docs` on the backend, but Caddy only forwards `/api/*`, so it 404s through the domain |

## SQLite Tables

The database here is handled more as cache than a system of record, this is mainly due to Alpaca always being treated as the source of truth. There is no in-memory layer anywhere in the backend, so a restart loses nothing. Everything Alpaca gives us lands in one of six tables. The data table defaults to `STOCKS_DB_PATH` (`backend/data/stocks.db` locally, the `stocks-data` Docker volume in production).

Currently there are 6 tables introduced serving multiple purposes:

1. store historical stock data at different granularity. The first two only stores these for the last 24h and 30d respectively. The last one stores the data permanently.
   - bars_minute
   - bars_hour
   - bars_days
2. Avoid unnecessary refreshes. This records when data was last fetched, so a request can tell quickly whether it needs to call Alpaca again or can just serve what is stored.
   - fetch_log: stores when some data was last fetched.
3. Stores up to date information for the stock leader board
   - Summaries
4. This stores config data about how the Alpaca data is formated as well as market open/close information.
   - meta

### `bars_minute` / `bars_hour` / `bars_days` — granularity/longevity tiers

Three tables with an identical schema, one per retention tier. They back the **1D**, **30D** and **ALL** ranges on the stock detail page.

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

| Table         | Alpaca timeframe | Serves      | Retention                                                      |
| ------------- | ---------------- | ----------- | -------------------------------------------------------------- |
| `bars_minute` | `1Min`           | `range=1d`  | 24 hours                                                       |
| `bars_hour`   | `1Hour`          | `range=30d` | 30 days                                                        |
| `bars_days`   | `1Day`           | `range=all` | never pruned — the all-time view grows one row per trading day |

The composite primary key does double duty: it is the upsert target, so
re-fetching an overlapping window can never duplicate a row, and it covers the
`(ticker, ts)` range scan every read does. At this scale (20 tickers, a few
thousand rows) no other index earns its keep.

`recorded_at` means _when this row's price was last recorded_, not _when we last
fetched it_. `upsert_bars` always refreshes `price` and `analytics` (a live
bar's volume keeps climbing even when its close hasn't moved) but only moves
`recorded_at` when the incoming price actually differs:

```sql
recorded_at = CASE WHEN bars_minute.price IS excluded.price
              THEN bars_minute.recorded_at ELSE excluded.recorded_at END
```

So the leaderboard's ~20 s minute-bar re-upsert of an unchanged close leaves the
stamp alone, while a real tick — or a corporate action such as a split
re-adjusting historical bars — restamps exactly the rows whose price changed.
That makes the "Stored data" table on the detail page honest about when a price
last moved.

### `fetch_log` — freshness, and the reason there is a separate table for it

```sql
CREATE TABLE IF NOT EXISTS fetch_log (
    tier       TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tier, ticker)
);
```

One row per `(tier, ticker)`, recording when that pair was last **successfully
fetched from Alpaca**. This is the source of truth for "is the cache fresh?",
and it is deliberately not derived from the bar rows. `recorded_at` would be
wrong for the minute tier: an actively traded ticker gets its stamp moved every
poll, so the tier would look permanently fresh and the intraday backfill would
never run. A legitimately empty fetch (a weekend, a thin IEX symbol) also stores
no bars but is still a fetch, and belongs here.

The leaderboard's snapshot fetch is stamped in the same table
under the pseudo-pair `(tier='summaries', ticker='*')`, so one freshness
mechanism covers everything.


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

Exactly 20 rows, ever — current state, no history, nothing to prune. `change`
and `change_percent` are derived on read rather than stored, so they can never
drift out of step with `price`/`previous_close`.

`upsert_summaries` writes all 20 rows _and_ the `fetch_log` stamp inside one
`BEGIN IMMEDIATE` transaction, so a reader never sees a half-written batch or a
stamp without its rows. Both upserts are monotonic — `... WHERE
excluded.fetched_at > summaries.fetched_at` — which means a slow writer holding
an older batch than what is already stored is silently ignored, and re-writing
the same batch is a no-op. That is what makes the table safe to write from
several processes sharing one file.

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
  `fetch_log` stamps (not the rows) so every tier is stale and gets refetched
  and overwritten in place. This runs once per change, not on every boot.
- **`market_clock`** — the latest Alpaca market clock as one JSON blob
  (`timestamp`, `is_open`, `next_open`, `next_close`, `fetched_at`). This is the
  cache behind `/api/market/clock`, and it is why the frontend no longer carries
  a hand-maintained NYSE holiday calendar. It is served with zero Alpaca calls
  until one of the boundaries it carries actually arrives: while `now` is before
  **both** `next_open` and `next_close`, the stored clock still describes the
  present no matter how old it is (a clock read on Friday afternoon is still the
  right answer on Sunday night). There is no TTL on top of that, because a timer
  would refetch clocks that are still correct — a browser polling every 20 s
  would be ~1000 Alpaca calls a day — to narrow the window on the one thing a
  boundary check cannot see: a session the exchange re-schedules _after_ we
  cached it. That case is picked up at the next boundary instead.

The clock fetch used to be leased under the pseudo-pair `('clock', '*')` in
`fetch_claims`, exactly like the bar tiers and the leaderboard — `CLOCK_TIER`/
`CLOCK_KEY` existed purely to be that lease key. Both the pseudo-pair and the
lease are gone along with `fetch_claims`; the clock refresh is guarded only by
its own `asyncio.Lock` now, the same in-process pattern as everything else.
`clock` was, and still is, deliberately absent from the tier list, so there is
no `bars_clock` table.

### How a request uses them

Every read path is the same three steps, whichever endpoint you hit:

1. **Is the stamp fresh?** (`fetch_log` for stocks and history, the boundary
   check for the clock.) If yes → serve straight from SQLite, zero Alpaca calls.
2. **If not**, take the in-process lock, and re-check (the worker you queued
   behind may have just refreshed, in which case there is nothing left to do
   — this double-checked freshness is the whole single-flight mechanism).
3. **Fetch, write, then serve.** The response is read back from the DB after the
   write, so what the browser sees is exactly what was persisted.

The backfill sweep (startup, then every 10 minutes) walks the same code paths
with no browser involved, so a cold box populates itself and a request and the
sweep can never double-fetch a pair or disagree about what "fresh" means.

## Error Handling

The guiding rule is that **stale data beats no data**. A user watching the board
during an Alpaca outage should see the last known prices greying out, not an
error page. Every response carries `is_stale` and `error` so the browser can
show *how* degraded the data is, and the layers below are arranged so that a
failure in one of them cannot take out the ones above it.

A quick map of what breaks and what the user actually sees:

| What fails | Backend response | What the user sees |
| --- | --- | --- |
| Alpaca slow, down or rate-limiting | `200` with stored rows, `is_stale: true` | Prices grey out, last values stay on screen |
| Alpaca returns junk for one ticker | `200`, that row flagged | One greyed row, the other 19 normal |
| Alpaca down *and* nothing ever stored | `503` (history / clock only) | Empty chart or hidden market banner |
| Unknown ticker or bad `range`/`tier` | `404` / `422` | Connection banner on the detail page |
| Backend process down | nothing (Caddy `502`) | Single "lost data communication" banner, last data retained |
| FX rate provider down | not involved | Native USD prices, "GBP rate unavailable" note |

### Alpaca — the upstream API

Every Alpaca call in the app is isolated in `alpaca_client.py`, so this is the
only module that has to know what "Alpaca is broken" looks like. Four distinct
failure kinds are handled:

- **Transport failures** — connection refused, DNS failure, TLS error, or the
  request exceeding `ALPACA_TIMEOUT_SECONDS` (5 s). Every `httpx.HTTPError`
  becomes an `AlpacaError`; no raw httpx exception ever escapes the module. The
  timeout also bounds how long a hung request can hold the in-process lock for
  that pair — the only thing serializing concurrent fetches now.
- **Rate limiting (429)** — flagged distinctly from every other failure, because
  the caller reacts to it differently: the leaderboard engages backoff, and the
  backfill sweep pauses `BACKFILL_RATE_LIMIT_PAUSE_SECONDS` before retrying that
  one pair.
- **Other non-2xx** — bad credentials (401/403), a symbol Alpaca rejects, or an
  upstream 5xx. All become a plain `AlpacaError` carrying the status.
- **Malformed 200s** — a response that *claims* success but does not carry what
  it should. This is the interesting one, so it gets its own section below.

An **empty but valid** response is explicitly not a failure: a weekend, a market
holiday or a thinly traded IEX symbol legitimately returns no bars, so
`fetch_bars` returns `[]` and the fetch is still stamped as successful. Treating
that as an error would make the backfill retry a weekend forever.

The two fetch contracts differ on purpose:

- `fetch_summaries` **never raises**. A request-level failure produces an
  all-error batch — every requested symbol present, each flagged — so the
  leaderboard always has 20 rows to render, even on a cold start with an empty
  table. A single bad symbol inside an otherwise good response degrades only
  that symbol.
- `fetch_bars` and `fetch_clock` **do raise**, and they fail atomically: no
  partial bar lists, even when the malformed bar arrives on page three of a
  paginated fetch. Their caller decides whether SQLite can cover for the outage.

### Validating what Alpaca sends back

A 200 is not a promise of well-formed data, and SQLite is dynamically typed —
it will happily store the string `"310.85"` in a `REAL` column, or `true` where a
price belongs. Junk that gets past this layer is not noticed at write time; it
surfaces much later as a chart that will not render or a leaderboard sorting
nonsensically. So every 200 is validated against the documented shape *before*
any field is trusted, and anything that fails validation is treated exactly like
a network failure — with the store left untouched.

What is checked:

- **The envelope.** The body must parse as JSON *and* be an object. An HTML
  error page from a proxy, a bare array, or a JSON scalar is rejected rather
  than indexed into.
- **Bar rows.** Each bar's `t` must be a string the timestamp normaliser can
  parse, and `o`/`h`/`l`/`c`/`v`/`vw`/`n` must all be present and genuinely
  numeric. Booleans do not count as numbers, numeric *strings* are rejected
  rather than coerced, and non-finite floats (`NaN`, `Infinity`) are rejected —
  each of those would otherwise land in the store as something that only breaks
  downstream.
- **Response identity.** A top-level `symbol` that does not match the symbol
  requested is rejected, so a mixed-up response can never be written under the
  wrong ticker.
- **Pagination.** `bars` and `next_page_token` must be of the expected type, and
  the token is validated *before* it is followed — a malformed token cannot send
  the paginator into an endless loop.
- **The market clock.** `is_open` must be a real boolean (the string `"true"` or
  the number `1` is rejected), and `timestamp`, `next_open` and `next_close` must
  all be parseable strings.
- **Snapshots** degrade per symbol instead of failing the batch: a wrong-typed
  value on a price path (`latestTrade.p`, `minuteBar.c`, `dailyBar.c`,
  `prevDailyBar.c`) turns that one symbol into an error entry, while a *missing*
  or null field simply falls through to the next fallback in the chain. A
  malformed `minuteBar` on an otherwise good snapshot keeps the summary and only
  skips the persistence step, logging a warning — the leaderboard should not
  degrade over a field it does not display.

Unknown extra keys are always ignored, so Alpaca adding a field never breaks us.
Timestamps are normalised to exactly `YYYY-MM-DDTHH:MM:SSZ`, converting real
offsets to UTC rather than relabelling them — the market clock serves New York
offsets, and getting this wrong would have shifted every boundary by four hours.
None of these validation failures is ever mistaken for rate limiting, so a
malformed body cannot accidentally trigger backoff.

### The backend itself

Errors that are the backend's own — a corrupt cache, a crashed worker, a bug —
are contained so that one broken thing does not cascade:

- **Reads never raise.** A missing database file, a missing table, or a corrupt
  stored value returns an empty result or `None` rather than propagating a
  `sqlite3` error. `get_market_clock()` treats malformed JSON, a missing field
  or a wrong-typed field as "nothing stored", which makes a corrupt row
  self-healing: it is simply refetched and overwritten.
- **Writes are all-or-nothing.** The leaderboard batch and its freshness stamp
  are written in one `BEGIN IMMEDIATE` transaction that rolls back on any
  exception, so a reader never sees half a batch or a stamp without its rows.
  Nothing is written at all on a failed fetch, which is what keeps the pair
  correctly marked stale for the next caller.
- **A crashed or hung worker cannot wedge the system.** The per-pair
  `asyncio.Lock` is released in a `finally` block, so even an unexpected
  exception mid-fetch frees it for the next caller — there's no lease to
  expire and no separate crash-recovery path to reason about. A worker that
  dies mid-fetch is, from the store's point of view, just another failed
  fetch: nothing gets written (see above), so the pair is left correctly
  marked stale and the very next request, or the next backfill pass, simply
  tries again.
- **The background sweep is failure-tolerant by construction.** An exception on
  one `(tier, ticker)` pair is logged and the sweep moves on to the next; a
  market-clock refresh failure is logged at WARNING and swallowed so it can never
  stop the bar-tier pass; and `CancelledError` is re-raised rather than absorbed,
  so shutdown still works.
- **Restarts cost nothing.** There is no in-memory cache to lose. The only
  process-local state is the backoff counters and the in-process locks, which
  are cheap to rebuild. `init_db` is idempotent and is also called defensively
  before writes, so even a wiped volume heals itself on the next request.
- **Liveness stays honest.** `/api/health` does no I/O at all, so it reports the
  FastAPI process as alive even when Alpaca and the database are both broken.
  That is deliberate: it is what the Docker healthcheck polls, and a health probe
  that fails because a third party is down would restart a perfectly healthy
  container.

A genuinely unexpected exception — a bug in our own code — still produces a
FastAPI `500`. Nothing in the app converts an *Alpaca* problem into a 500; a 500
means the backend itself is wrong. The gap worth naming: there is no global
exception handler shaping those into the same `{is_stale, error}` envelope the
rest of the API uses, so an unhandled bug surfaces as FastAPI's default error
body and the frontend treats it as a generic query failure.

### The frontend

The browser assumes any given render may have missing, stale or partial data:

- **Query failures.** TanStack Query retries three times before surfacing an
  error, and the previously fetched data stays in the cache throughout — a
  failed poll never blanks a populated screen.
- **Row-level degradation.** A stale or errored row greys out and keeps its last
  price. Error strings are never rendered on a row; a broken ticker reads as
  dimming, not as a stack trace in the middle of the board.
- **Absent data renders as absent.** The market-status banner returns nothing at
  all until the clock has loaded, rather than guessing "closed" and flipping a
  second later. Chart points with unparseable timestamps are dropped rather than
  plotted at epoch zero.
- **The FX call fails independently.** It is a separate provider from a different
  host, so when it fails the prices fall back to native USD and the disclosure
  note reads "GBP rate unavailable". Nothing goes blank and no other query is
  affected.

Two honest gaps here: there is no React error boundary around the tree, so an
exception thrown during render would blank the page rather than degrade it; and
the detail page cannot currently distinguish the API's `404` for an unknown
ticker from the backend being unreachable — both render the same connection
banner.

### Between the frontend and the backend

This is the failure the user is most likely to actually hit, and it is treated as
its own case rather than as twenty simultaneous row errors:

- **The backend being unreachable** — container down, box rebooting, network cut,
  or Caddy returning a `502` because the upstream is not answering — surfaces as
  exactly **one** page-level `ConnectionBanner` ("we have lost data communication
  with the backend"), with the last successfully fetched data left on screen
  behind it. Twenty greyed rows plus one banner, not twenty error messages.
- **The poll countdown doubles as a liveness indicator.** The brass bar in the
  header drains over the 20 s poll, pulses while a fetch is in flight and refills
  when fresh data lands — so a stalled connection is visible before the user has
  read the banner.
- **CORS and origin mismatches** land in the same bucket. In production the page
  and the API share one origin behind Caddy, so CORS cannot fail; in development
  a wrong `ALLOWED_ORIGINS` shows as a blocked request in the console and the
  same banner in the UI.
- **Recovery is automatic and requires no user action.** The polls keep running
  on their interval, and the first one that succeeds clears the banner and
  refreshes the board. There is no reload button because there is nothing for it
  to do.

### Status codes at a glance

`200` for everything that has data to show, including stale data. `404` for an
unknown ticker, `422` for a bad `range` or `tier`, and `503` only when Alpaca
failed *and* the database holds nothing at all for that resource — the one case
where there is genuinely nothing to render. Nothing in the app returns a `500`
for an Alpaca reason.

## Limitations

- **SQLite is single-process.** The data itself would survive several backend
  processes sharing one database file — the writers are conflict-tolerant on
  their own (monotonic upserts, `(ticker, ts)` primary keys) — but single
  flight is purely in-process now, so more than one process would just
  duplicate Alpaca fetches instead of coordinating around them. Run exactly
  one backend process per database file; horizontal scaling means moving to
  Postgres first, not adding processes or instances.
- **One box, no redundancy.** A single EC2 instance with no load balancer and no
  standby: a reboot is a brief outage, and an instance loss is a rebuild.
- **The universe is fixed at 20 hard-coded US large caps** (`tickers.py`). Adding
  a stock is a code change and a redeploy — there is no admin UI and no
  per-user watchlist.
- **Free-plan data.** Alpaca's free tier is the IEX feed, which is a fraction of
  total US volume, so prices can differ slightly from a consolidated
  (SIP) feed and thinly traded symbols have gappier intraday bars.
- **No authentication, no per-client rate limiting.** The API is public and
  read-only. That is survivable because it is DB-first — an abusive client
  mostly hits SQLite rather than our Alpaca quota — but there is nothing
  stopping someone from hammering it.
- **GBP conversion happens in the browser**, using a third-party ECB rate
  (Frankfurter) fetched hourly by the client. The backend never sees GBP, and
  that one call is the only external dependency the frontend has of its own.
- **A re-scheduled session is picked up late.** The cached market clock is only
  invalidated by a boundary crossing, so an unscheduled closure announced
  mid-session is not reflected until the next boundary.
- **A ≥30-minute in-session data hole looks like a closure** on the 1D chart.
  The chart greys any gap of half an hour or more and labels it "market closed";
  since dropping the hardcoded calendar, it has no way to distinguish a real
  session break from an outage. Fetching Alpaca's `/v2/calendar` would fix it.
- **The dashboard page is placeholder cards only**, by design.
- **No CI.** Tests are green locally and run by hand; nothing enforces that on a
  push.
- **Company logos are generated letter marks**, not official brand assets, and
  the display font is a personal-use-only demo build — both would need
  replacing or licensing before any commercial use.

## Tradeoffs

**SQLite as the only cache, rather than Redis or an in-process TTL cache.** The
whole dataset is ~20 rows of current state plus a few thousand bars, and it has
to survive restarts anyway. One file gives persistence and the "Stored data"
inspection view for free, with no extra service to run or fail. The cost is
the single-process ceiling above.

**The database _is_ the cache — no in-memory layer.** A restart therefore costs
nothing and two processes can never disagree about freshness. The cost is a disk
read on every request; at this size that is microseconds, and it buys a property
that is otherwise fiddly to get right.

**Boundary-based clock expiry instead of a TTL.** Roughly two Alpaca clock calls
a day instead of ~1000, with the open/close flip still landing on the exact
second. The cost is the late-rescheduling window described above — rare, and
recoverable at the next boundary.

**Serve stale rather than fail.** A dashboard that shows five-minute-old prices
during an outage is more useful than one showing an error, so degradation is
flagged in the payload (`is_stale`/`error`) and rendered as dimming. The cost is
that a user must read the visual cue to know the data is old — mitigated by the
poll countdown in the header and the per-row greying.

**Collapse concurrent fetches rather than dodge them.** A burst of polls that
all find the same pair stale queue on its `asyncio.Lock`; the first one
fetches, and the rest simply re-check freshness once they get the lock and
serve what already landed — never a second Alpaca call. The price is that a
waiter can be held for up to one Alpaca round trip (capped by
`ALPACA_TIMEOUT_SECONDS`) instead of getting slightly-stale data back
immediately; at 20 tickers and a 5 s timeout that has never been the
bottleneck.

**In-process single flight instead of a cross-process lease.** There used to
be a `fetch_claims` lease table backing this up across backend processes —
two extra write transactions and a `PRAGMA`/`CREATE TABLE IF NOT EXISTS` on
every fetch attempt, plus a fencing token and a config setting worth of
complexity. It bought nothing in practice, because production only ever runs
one `uvicorn` process. Removing it trades away multi-process safety the app
was never exercising for a simpler, cheaper single-flight mechanism; turning
`--workers N` back on would silently start wasting Alpaca quota (still
correctly — the write paths stay conflict-tolerant on their own) rather than
sharing the fetch the way it used to.

**Backoff on the leaderboard but not on history/clock.** The leaderboard is the
one call every open browser makes on the same schedule, so a failure there
multiplies; history and the clock are cheap and per-resource, and backing them
off would just make recovery slower.

**A fixed 20-ticker universe.** Keeps the leaderboard a single batched snapshot
call — one Alpaca request per poll for the whole board, comfortably inside the
free tier — and makes the data volume predictable. The cost is that the universe
is a code change.

**FX in the browser.** The backend stays purely an Alpaca cache and the rate is
shared by every price on the page from one hourly call. The cost is a
client-side external dependency with no stale-cache protection of its own; moving
it behind the backend, with the same treatment Alpaca gets, is the obvious next
hardening step.

**A local mock Alpaca server instead of recorded fixtures.** Development and
demos work offline, with no keys and no quota, and — crucially for this app —
the mock can be told to serve a market that is _currently closed_, which is
otherwise only observable at specific times of day. The cost is a second
implementation of Alpaca's response shapes to keep honest.

**Split-adjusted bars by default** (`adjustment=split`, where Alpaca's own
default is as-traded `raw`). A split otherwise draws as a price cliff on the
all-time chart. Changing the setting invalidates the stored bar stamps once so
existing rows are refetched rather than left inconsistent.

## Running It Locally

Clone the repo:

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

- Frontend: <http://localhost:5173>
- Backend: <http://localhost:8000> (Swagger UI at `/docs`)

Both services bind-mount their source for hot reload, and the SQLite file lives
in the named volume `stocks-data`, so it survives rebuilds. Without keys the app
still starts — every Alpaca call just fails and the board renders greyed rows.

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

The SQLite file is created at `backend/data/stocks.db` (git-ignored). The
frontend reads `VITE_API_BASE_URL` (defaults to `http://localhost:8000`; set it
in `frontend/.env.local` if you move the backend port).

### Option C — Offline, against the mock (no keys, no network)

`mock/alpaca_mock.py` is a stdlib-only stand-in for the three Alpaca endpoints
the backend calls (`/v2/stocks/snapshots`, `/v2/stocks/{symbol}/bars`,
`/v2/clock`). It serves a deterministic sine wave — every symbol oscillates
between 80 and 120 on a 20-minute cycle, each with a different phase so the
leaderboard keeps re-ranking.

```bash
./mock/dev.sh                     # mock on :8500 + backend on :8000, Ctrl-C stops both
cd frontend && npm run dev        # in another terminal

# or as a Compose overlay
docker compose -f docker-compose.yml -f docker-compose.mock.yml up --build
```

The script points both `ALPACA_DATA_BASE_URL` and `ALPACA_TRADING_BASE_URL` at
the mock and uses a **separate** database (`backend/data/mock.db`), so synthetic
bars are never mixed into real Alpaca data. `curl localhost:8500/healthz` shows
the active curve parameters. Two flags matter for testing the closed-market UI:
`--sessions-only` emits bars only inside the session window, so the 1D chart
shows its greyed "market closed" block, and the mock clock reports the market
open 13:30Z–20:00Z _every_ day, so both banner states are reachable whenever you
happen to be working.

### Tests

```bash
cd backend  && uv run pytest     # 287 tests — Alpaca mocked, SQLite in tmp dirs
cd backend  && uv run ruff check app tests
cd frontend && npm run test      # 262 tests across 32 files — API and FX mocked
cd frontend && npm run build     # tsc -b type-check + production build
```

No test touches the network or real credentials. What each file covers is in
[The Test Suite](#the-test-suite) below.

## Deployment

Production is one small EC2 instance (Amazon Linux 2023, t3.micro with 2 GB
swap) running two containers under Compose:

```
browser ──:80/:443──> [ Caddy ] serves the built React SPA (dist/)
                         │  /api/* ──> [ uvicorn / FastAPI ] ──> Alpaca
                         │                     │
                         │                SQLite (named volume)
                    Let's Encrypt (automatic when DOMAIN is set)
```

Only Caddy is published; the backend is reachable solely through the `/api`
proxy, so the page and the API share one origin and CORS is moot. Config and
secrets come from a `.env` file on the box (`cp .env.example .env`, `chmod 600`,
never committed), including `DOMAIN` — leave it empty for plain HTTP on the IP,
or set it (currently a DuckDNS name) and Caddy obtains and renews the
certificate by itself.

Deploying is a pull and a rebuild on the box:

```bash
cd /opt/m3
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
```

Rolling back is the same command after `git checkout <previous-sha>`. The
backend image runs as a non-root user with locked runtime-only dependencies, and
carries a `/api/health` healthcheck that Compose polls every 30 s. There is
nothing to back up: SQLite here is a re-fetchable cache, and the backfill sweep
repopulates it within a minute or two of a cold start.

### How the deployment could be improved

Roughly in order of what I would do first:

1. **CI on every push** — run `pytest`, `vitest` and `tsc -b` in GitHub Actions
   and make a green run a precondition for deploying. Right now the tests are
   only as reliable as my remembering to run them.
2. **Build images in CI, not on the production box.** Today a deploy runs
   `npm ci` + a Vite build on a 1 GB instance — that is why the Dockerfile caps
   Node's heap and why the box needs 2 GB of swap, and it means a build failure
   happens _in production_, mid-deploy. Building and pushing tagged images to a
   registry (ECR/GHCR) turns deployment into "pull this digest and restart",
   makes rollback a tag change rather than a rebuild, and removes the toolchain
   from the server entirely.
3. **Zero-downtime restarts.** `up -d --build` stops the old container before the
   new one is healthy. Starting the replacement, waiting on `/api/health` and
   only then cutting Caddy over removes the few-second gap.
4. **Restore the deployment runbook to the repo.** `AWS-DEPLOY.md` documented the
   instance setup, security-group rules, day-2 operations and the HTTPS
   switch-on; it was removed and `docker-compose.prod.yml` still refers to it.
   That knowledge should live next to the code it describes.
5. **Real observability.** Today diagnosis is `docker compose logs`. Structured
   JSON logs, an uptime check against `/api/health`, and a few counters that
   matter (Alpaca calls, 429s, cache-hit ratio, sweep duration) would turn "the
   board looks stale" into an answerable question. The app already logs the
   right events; nothing collects them.
6. **Automated log rotation and disk hygiene.** An 8 GB root fills with dangling
   images and container logs; `docker image prune` currently happens when
   someone remembers. Compose log caps plus a scheduled prune fixes it.
7. **Infrastructure as code.** The instance, security group and Elastic IP were
   created by hand in the console. A short Terraform module would make the box
   reproducible and document itself.
8. **Staging environment.** The mock Compose overlay already provides a
   keyless, deterministic full stack — running it as a deployed staging target
   would let a release be smoke-tested before it reaches production.
9. **A second instance (and therefore Postgres).** Only worth it if uptime
   requirements change: SQLite is what pins the app to one host, so this is a
   database migration first and a load balancer second.

## The Test Suite

**287 backend tests** (pytest) and **262 frontend tests** (vitest, 32 files).
Nothing in either suite touches the network or a real credential: Alpaca is
faked, SQLite runs in throwaway directories, and the browser suite mocks the
API module.

The backend files are organised by *layer*, not by feature, and each one fakes
the world at a different depth. That is the thing to understand before adding a
test — putting it in the wrong file usually means mocking the wrong thing.

### Backend — `backend/tests/`

| File | Tests | The layer it exercises | Where the fake sits |
| --- | --- | --- | --- |
| `test_alpaca_client.py` | 86 | The HTTP boundary | `httpx.MockTransport` — real client code, fake socket |
| `test_storage.py` | 77 | The SQLite store | nothing is mocked; a real DB in a tmp dir |
| `test_stocks_router.py` | 53 | Endpoint contracts | `fetch_summaries`/`fetch_bars` patched at the router's call site |
| `test_leaderboard_table.py` | 31 | DB-first behaviour end to end | counting `MockTransport` — assertions land on the HTTP boundary |
| `test_market_router.py` | 22 | The market clock endpoint | `fetch_clock` patched at the router's call site |
| `test_backfill.py` | 17 | The background sweep | `fetch_bars` patched; sweep driven directly |
| `test_health.py` | 1 | Liveness | nothing to fake |

**1. `test_alpaca_client.py` — does the app survive what Alpaca actually
sends?** Every test drives the real client over a mock transport, so request
shape and response handling are both covered. It asserts the request itself
(correct host per endpoint — data vs trading — the `APCA-API-*` headers,
`feed=iex`, `adjustment=split`, `start` but no `end`), the error taxonomy
(network failure, 429 flagged distinctly from other non-2xx, malformed body),
and the validation rules in detail: non-object bodies, bars with missing or
non-numeric fields, booleans and numeric strings rejected, mismatched `symbol`,
a bad `next_page_token` caught before it is followed, `is_open` that is not a
real boolean. It also pins the two contracts apart — `fetch_summaries` never
raises and degrades per symbol, `fetch_bars`/`fetch_clock` raise atomically —
and that timestamps normalise to `…Z` with real offsets converted, not
relabelled.

**2. `test_storage.py` — does the store keep its promises?** No mocking at all:
this is the real persistence layer against a real SQLite file. Table creation
and idempotent `init_db`, the `(ticker, ts)` upsert (re-fetching an overlapping
window must not duplicate), the `recorded_at` gating rule (a same-price
re-upsert leaves the stamp alone, a real change restamps), retention and
`prune` boundary behaviour (a row exactly at the cutoff survives), `fetch_log`
stamping and tier validation, the monotonic `summaries` batch, the `meta`
key/value helpers including the market clock round-trip and its
corrupt-value-returns-`None` contract, and the two schema migrations
(`ensure_bars_adjustment` invalidating stamps once, and the one-time
`fetch_claims` drop).

**3. `test_stocks_router.py` — does each endpoint honour its contract?** Alpaca
is patched at the router's call site here, so these tests are about HTTP
surface rather than client behaviour: response shapes, `404` for an unknown
ticker, `422` for a bad `range`/`tier`, `503` only when nothing is stored,
per-tier freshness windows, the derived `change`/`change_percent`, and the
"no straggler" rule where the daily tier's fetch reaches back to the oldest
stored bar.

**4. `test_leaderboard_table.py` — does the DB-first design hold under
pressure?** The most valuable file, and the one that would catch a regression
in this app's central claim. It drives the *real* client over a transport that
counts every request, so "exactly one Alpaca call" is asserted where it
matters. It covers a concurrent burst collapsing into one fetch, waiters not
retrying after a leader fails, exponential backoff, a late writer being unable
to overwrite newer data, atomic row+stamp writes, a simulated process restart
serving from the DB, the sweep repopulating a cold board, and — since the
lease was removed — that no lease table is created and a legacy one cannot
block a refresh.

**5. `test_market_router.py` — is the clock cached on the right rule?** Boundary
expiry with no TTL: a stored clock is served with zero Alpaca calls however old
its `fetched_at` is, and refetched the moment `next_open`/`next_close` passes.
Also the failure policy (stale with the error attached, `503` only on an empty
store), `refresh_clock` swallowing Alpaca errors for the sweep, and app wiring.

**6. `test_backfill.py` — does the sweep behave when nobody is watching?** That
it refreshes lapsed pairs and skips fresh ones, pauses and retries once on a
rate limit, logs and moves on from other failures, shares the refresh path with
the endpoints, and honours cancellation at shutdown.

**Shared conventions.** Wall clocks are frozen and advanced by hand
(`fake_utcnow` for freshness, `fake_clock` for backoff windows) so nothing
sleeps and no test is time-of-day dependent. `config.DB_PATH` is monkeypatched
per test, giving every test its own database. Router module state (locks,
backoff) is reset between tests, and resetting it mid-test is how a "process
restart" is simulated. The autouse fixture in `conftest.py` disables the
backfill sweep, so entering the app's lifespan in a test never kicks off a real
one — `test_backfill.py` re-enables it where the sweep is the subject.

### Frontend — colocated with the code

Every component owns a `Component.test.tsx` next to its `.tsx`, `.props.ts` and
`.css`. Components never talk to the network in a test: `vi.mock` replaces the
`api/queries` module, so a test states the query result it wants and asserts
what renders. There is no MSW layer and no test server.

| Area | Tests | What is being checked |
| --- | --- | --- |
| `components/` | 149 | Rendering, interaction, degraded states (stale dimming, error badges, banners) and a11y roles/labels |
| `utils/` | 45 | Pure logic in isolation — ranking, GBP conversion, countdown formatting, intraday gap detection, FLIP measurement |
| `pages/` | 29 | Composition only: that the right children are present and wired, since pages hold no logic |
| `api/` | 25 | Query keys, poll intervals and `staleTime`, request URLs, and the FX rate hook |
| `providers/` | 10 | Context wiring (query client, FX rate sharing) |
| `hooks/` | 4 | `useHistoryRange` URL sync and the poll countdown |

Fake timers cover anything that ticks — the 1 s market-status countdown, the
20 s poll bar — so a test asserting a countdown never waits for one. `npm run
build` runs `tsc -b` first, so type errors fail the build even though vitest
would not catch them.

**What the suite cannot see.** jsdom does no layout, so the leaderboard's
re-rank animation is asserted through classes and data rather than movement;
there is no browser-level end-to-end layer at all; and the mock Alpaca server
is verified by hand rather than by tests. Those gaps are the subject of the
next section.

## Additional Tests Worth Adding

The suites today are 284 backend and 262 frontend tests, all hermetic — Alpaca is
mocked at the `httpx` transport, SQLite runs in tmp directories, the frontend
mocks the API module. That covers unit behaviour and per-endpoint contracts
well. The gaps are the seams between the pieces:

- **End-to-end browser tests.** There is no Playwright/Cypress layer at all
  (there was one small script for the leaderboard re-rank animation; it has since
  been removed). Real-browser coverage of the core journeys — board loads and
  re-ranks, detail page switches range and keeps the chart mounted, the
  connection banner appears when the API dies — would catch what jsdom
  structurally cannot, starting with anything involving layout or animation.
- **Contract tests against the mock server.** The mock is currently verified by
  hand with `curl`. Running the real backend against it in-process and asserting
  the full request/response cycle would test the client, router and storage
  together, and would keep the mock's response shapes honest against
  `alpaca_client`'s validation.
- **A recorded-fixture test against real Alpaca payloads.** One saved response
  per endpoint, replayed through the client, would catch the day Alpaca changes a
  field name — something a hand-written mock can never notice.
- **Real concurrency tests.** The single-flight logic is tested with mocked
  locks and sequenced calls, not real concurrency. Actually firing a burst of
  concurrent requests at one running backend would test the `asyncio.Lock` and
  double-checked freshness as designed rather than as modelled. Since only one
  process is ever expected to run against a database file now, spawning two
  processes against one DB file is no longer a single-flight test — it's a
  stress test of the write paths that were always meant to tolerate that
  (monotonic `summaries` upserts, `(ticker, ts)` bar upserts). The assertion
  worth making there is not "exactly one Alpaca call" but that the *data*
  converges correctly no matter how many calls landed: no `database is locked`
  errors under load, and the stored rows end up matching whichever fetch was
  actually newest.
- **Property-based tests for the freshness and boundary rules.** `_is_fresh`,
  the tier windows and the backoff schedule are pure functions over timestamps —
  ideal for Hypothesis, which would explore the ordering edges (equal
  timestamps, boundaries crossed exactly on the second, clock skew) more
  thoroughly than hand-picked cases.
- **Migration/upgrade tests.** `ensure_bars_adjustment` and the one-time
  `DROP TABLE IF EXISTS fetch_claims` cleanup in `init_db` are the two
  schema-evolution paths now. Starting from a checked-in old-format database
  file — one still carrying a `fetch_claims` table from before the cleanup —
  and asserting the upgrade preserves data would make those safe to change.
- **Load and quota tests.** Assert that N concurrent pollers over M minutes
  produce a bounded number of Alpaca calls. The free tier's 200 req/min is a real
  constraint, and the cost model is currently reasoned about rather than
  measured.
- **Accessibility assertions.** Roles and `aria-label`s are checked ad hoc on a
  few components; an `axe` pass over each rendered page would cover the rest,
  including colour contrast on the stale/greyed states.
- **Visual regression on the chart.** The 1D chart's greyed market-closed blocks
  are asserted through data structures, not pixels. Snapshot images of a few
  fixed series would catch rendering regressions the unit tests are blind to.
- **Failure-injection tests in the browser.** The API mock always resolves;
  driving it through slow responses, mid-poll failures and recovery would
  exercise the stale-then-recover transitions users actually see during an
  outage.
