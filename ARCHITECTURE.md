# Architecture Decisions

Why the code looks the way it does. [`README.md`](README.md) is the canonical
description of *what* the system does — the SQLite schema, the error-handling
policy, the endpoint contracts, the deployment topology and the tradeoffs behind
them. This file holds only the code-level rationale that has no home there: the
decisions a reader would otherwise have to reverse-engineer, and the ones a
future contributor could plausibly undo by accident.

Nothing here is duplicated from the README. Where a topic is covered there, this
file links to it instead of restating it.

---

## Backend

### Config is read at call time, never imported by name

Every module reads settings as `config.NAME` — `from app import config` at the
top (or inside the function), then `config.DB_PATH` at the point of use. Never
`from app.config import DB_PATH`.

This is load-bearing, not stylistic. The entire test suite reconfigures the app
by monkeypatching attributes on the `config` module: a per-test tmp database via
`monkeypatch.setattr(config, "DB_PATH", ...)`, the backfill sweep disabled via
`BACKFILL_ENABLED`, the Alpaca hosts pointed at a mock. A name imported at module
load time binds the value once and silently ignores every later patch. The
failure mode is nasty — nothing raises, tests just quietly exercise the wrong
database or hit the real network — so the pattern is noted at each site
(`config.py`, `storage.py`, `alpaca_client._client`, `backfill.py`). `storage.py`
even carries `from app import config  # noqa: F401` purely to keep the module
reference alive for this.

### SQLite: a fresh connection per call, WAL, no pool

`storage.py` opens a new `sqlite3.connect(config.DB_PATH)` on every call and sets
`PRAGMA journal_mode=WAL`, rather than holding a pooled or long-lived connection.

This looks like an oversight and is not. Every query here is tiny (at most a few
thousand rows), and it runs synchronously inside a request handler that is
already serialised behind an `asyncio.Lock`. Connection setup costs microseconds
against an Alpaca round trip measured in hundreds of milliseconds — the thing the
lock actually exists to collapse. WAL is what keeps concurrent readers from
blocking on the occasional writer. A connection pool would add lifecycle and
thread-affinity problems to buy nothing measurable.

### The backfill sweep's Alpaca call budget

The sweep only refetches tiers whose `fetch_log` stamp has lapsed, which keeps
its cost far below the free tier's 200 req/min:

| Situation | Calls |
| --- | --- |
| Steady state, minute tier | ~20 per 10-minute interval |
| Steady state, hour tier | ~20 per hour |
| Steady state, daily tier | ~20 per day |
| Worst case (every pair lapsed after a long outage) | 60 in one pass |

That headroom is the reason the sweep itself carries no rate limiting of its own,
beyond pausing on an explicit 429.

### Backoff is sized independently of the poll interval

`BACKOFF_BASE_SECONDS` (90s) and `BACKOFF_MAX_SECONDS` (600s) are fixed defaults,
deliberately *not* derived from `CACHE_TTL_SECONDS` (20s).

The backoff window has to stay meaningfully longer than several frontend polls.
During an Alpaca outage every open browser keeps polling every 20 seconds; if
backoff were pegged to that interval, each poll would re-trigger a fresh Alpaca
attempt instead of being served stale rows from SQLite. Deriving one from the
other also makes the two constants fight whenever either is tuned.

### The daily tier reaches back to the oldest stored bar on every refresh

`_fetch_start` widens the daily-tier request back to the oldest bar already
stored, not just on the first fetch. That sounds like it grows without bound, but
at ~252 trading days per year against Alpaca's 1000-bar page size, even a
multi-year history fits comfortably in a single paginated `1Day` request. That is
what makes the "no straggler left un-refreshed" rule cheap enough to run on every
refresh instead of needing a separate one-off backfill job.

### The timestamp format is a cross-module invariant

`alpaca_client.normalize_timestamp` guarantees exactly `YYYY-MM-DDTHH:MM:SSZ`,
converting real offsets to UTC rather than relabelling them. `storage.py` depends
on that guarantee: every `WHERE ts >= ?`, `ORDER BY ts` and `MAX(recorded_at)`
uses plain lexicographic string comparison instead of parsing dates, which is
only correct because the format is fixed-width and UTC-normalised.

Neither module states the whole invariant alone, and loosening either end — a
variable-width fractional-seconds field, or a stored non-UTC offset — breaks
range queries silently rather than loudly.

> Deployment topology (one uvicorn process per database file, and why single
> flight is in-process only) is covered in the README's
> [Limitations](README.md#limitations).

---

## Frontend

### Every polled query lands on one shared wall-clock tick

`msUntilNextPoll()` returns the time until the next wall-clock multiple of
`POLL_INTERVAL_MS`, and `alignedPollInterval()` is what every polled query passes
as its `refetchInterval` — so refetches are scheduled on a *shared* tick, not "20
seconds after my own last fetch".

Without this, queries that mount at different moments (switching 1d → 30d → all,
a cached range remounting, the detail/chart/table trio) drift into independent
timers. The visible symptom is the header countdown: one clean cycle draining to
zero, versus a ragged bar jumping to partial fills as each timer fires.

### Re-render isolation: `React.memo` plus module-level selectors

The 20-second poll must not re-render the page tree. Two mechanisms do this
together, and both are easy to undo by "simplifying":

- **Module-level `select` functions.** `useStockDetailQuery` accepts a `select`,
  and call sites pass a function defined at module scope (e.g. `selectIdentity`
  in `StockDetailPage.tsx`), never an inline arrow. React Query applies
  structural sharing to the selected value, so a component that needs only
  `name`/`sector` keeps a stable reference across polls — *but only if the
  selector itself is referentially stable*. Inlining it re-runs the selector every
  render and throws the benefit away. Observers with and without `select` share
  one cache entry, so this costs no extra requests.
- **`React.memo` boundaries.** `StockRow` bails out on an unchanged `stock`
  reference plus unchanged rank delta. `PriceTicker` subscribes only to
  `useStockDetailQuery` and never the history query, so a chart poll cannot
  re-render it or vice versa. `RawDataTable` is the heaviest subtree on the page
  (~1,440 rows in the minute tier) and re-renders only for its own query data, a
  tier pick, or a ticker/range change.

`PriceTicker.test.tsx` guards this with hand-rolled reactive stores rather than a
static `vi.mock` return value — a static mock cannot tell *which* hook a component
actually depends on, so flattening that helper back to `mockReturnValue` would
silently stop testing the thing it exists to test.

### The stored-data table's column, tier and degradation contract

`RawDataTable` is the raw view of the backend's SQLite store — every row of one
tier's `bars_<tier>` table, every column (`ts`, `price`, the stored Alpaca
analytics, `recorded_at`), newest first, plus per-tier row counts and the tier's
last successful Alpaca fetch. It is backed by `useStoredDataQuery(ticker, tier)`
and polls only that query, never the detail or history ones.

- **Tier selection.** Defaults to the tier behind the page's history `range` and
  follows it when the range changes; the in-card switch overrides that for the
  current range only.
- **Units live in the headers.** Every `<th>` carries its unit as plain text so it
  is part of the header's accessible name ("Close (USD)") — the response's
  `currency` for stored money columns (falling back to USD), `shares` / `count`
  for volume and trades, UTC for timestamps.
- **The GBP column is additive.** "Close (GBP)" converts the stored close at the
  shared `FxRateProvider` rate and simply disappears when no rate is available.
  The stored figures are never altered — this view is the raw data.
- **Failure greys, never replaces.** When the query fails the last received rows
  stay put and the card dims; no error text takes the table's place.

### `usePollCountdown` defers to a microtask

React Query can emit cache events synchronously *inside another component's
render* — it fires `added` while constructing a new query observer during
`useQuery`'s render path, which happens on the first visit to a range or tier, or
on a new ticker. Calling `setState` straight from that callback is a
cross-component update during render: React warns, and the render is wasted.

The hook routes evaluation through `queueMicrotask` instead. That still runs
before paint, and it coalesces a burst of cache events into a single evaluation.

### The leaderboard re-rank is a FLIP animation

Rows animate between ranks with a First/Last/Invert/Play pass. Each slot's `top`
is measured relative to the rows container — never the viewport, or scrolling
between polls would read as movement — and compared with the previous commit. A
moved slot is snapped back with transitions disabled, the style is flushed with a
forced reflow (`void slot.offsetHeight`), then released so the stylesheet
transition carries it home with a per-rank stagger.

The effect is keyed on `stocks` **only**. Re-measuring on any other re-render —
the rank-delta state update, for instance — reads mid-flight transformed
positions as real movement and cancels the slide. Direction classes
(`is-moved-up` / `is-moved-down`) drive both the colour/z-index treatment and the
`▲n` / `▼n` chip, cleared after `MOVED_EMPHASIS_MS` / `RANK_DELTA_MS`.

### The header condense is a two-beat swap, not a cross-fade

Scrolling past `CONDENSE_SCROLL_Y` swaps the header text in three timed stages:
the outgoing text fades out over `--swap-out` (600ms), the bar carries no text for
`--swap-gap` (350ms), then the incoming text fades in over `--swap-in` (900ms),
delayed by `--swap-in-delay = swap-out + swap-gap`. The bar's own colour and
padding shift run underneath across the full `--bar-shift` (1400ms).

One cross-file coupling to know about: `--countdown-color` is defined on
`.app-shell__bar` but consumed in `PollCountdownBar.css` as
`var(--countdown-color, var(--color-accent))`. That is how the poll countdown
inherits the header's brass/white state — renaming or removing the variable
breaks `PollCountdownBar`'s colour, not `AppShell`'s, which is not where anyone
would look.

### Market-closed gaps are detected from the data, not a calendar

The 1D chart's x-axis is categorical and the client carries no session calendar —
the market clock lives in the backend and only describes *now*, not history. So
the rule is purely data-driven: any hole of at least `MIN_HOLE_MS` (30 minutes)
between consecutive bars becomes a fixed-width greyed placeholder block.

This correctly marks overnights, weekends and holidays. The known cost is that a
long in-session Alpaca outage is drawn identically to a closed market; with no
calendar the client cannot distinguish them. Fetching Alpaca's `/v2/calendar`
would fix it.

### The market-status banner is deliberately not a live region

`MarketStatusBanner` uses `role="status"` with `aria-live="off"`. A region whose
text changes every second would have a screen reader talking over everything
else. The accessible name carries the same state rounded to minutes instead, so a
reader landing on it hears "Market closed, opens in 4 hours 17 minutes" rather
than ticking seconds.

Separately: when the local countdown hits zero, the displayed backend clock is
known to be stale — the boundary it was counting toward has arrived. The component
refetches exactly once per boundary crossing (tracked with a ref keyed on the
boundary's ISO timestamp) and clamps the display at `0m 00s` until the new clock
lands, rather than refetching on every tick.

### The visual direction behind the design tokens

The palette and typography in `global.css` are modelled on *the official list,
re-papered*: British institutional print — the daily official list, Bank of
England stationery — on warm pastel-yellow ledger stock. Navy ink, brass
hairlines, Gill Sans for anything that speaks, monospace for anything numeric,
and racing green / claret for up and down instead of traffic-light RGB.

Written down because the token values look arbitrary without it, and the next
person to add a colour should know what they are matching.

---

## Testing

The shared conventions — frozen `fake_utcnow` / `fake_clock` wall clocks, the
per-test `config.DB_PATH` monkeypatch, `conftest.py`'s autouse disabling of the
backfill sweep — are documented in the README's
[Test Suite](README.md#the-test-suite) section.

One thing that section does not name: **the Alpaca HTTP seam is
`alpaca_client._transport`.** Tests inject with
`monkeypatch.setattr(alpaca_client, "_transport", httpx.MockTransport(handler))`,
which drives the *real* client code — URL building, headers, pagination, error
mapping and response validation — over a fake socket. That is a different depth
of test from patching `fetch_summaries` / `fetch_bars` at the router's call site,
which is what the router tests do. Picking the wrong one is the usual reason a new
test mocks the wrong layer.

---

## Mock Alpaca server

### Bar extrema are computed analytically

`mock/alpaca_mock.py` derives each synthetic bar's high and low in closed form —
checking both endpoints plus the first peak or trough of the sine curve falling
inside the bar — rather than sampling the interval.

This was a fix, not premature optimisation: sampling made a multi-day `1Day` bars
request take about five seconds, which tripped the backend's
`ALPACA_TIMEOUT_SECONDS` and made the mock look like a broken upstream.
