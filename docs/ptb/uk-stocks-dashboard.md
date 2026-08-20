# PTB: UK Stocks Dashboard

## Status
- stage: done (v3 complete: backend 44/44 pytest, frontend 108/108 vitest; pending live smoke test)
- plan version: 3
- cycle: 2
- test-build files:
  - Scaffold (pre-agents): .gitignore, docker-compose.yml, README.md, backend/{pyproject.toml,Dockerfile}, backend/app/{__init__.py,config.py,tickers.py,schemas.py}, backend/app/routers/__init__.py, backend/tests/__init__.py, frontend/{package.json,vite.config.ts,tsconfig.json,tsconfig.node.json,index.html,.env.local,Dockerfile}, frontend/src/{setupTests.ts,vite-env.d.ts}, frontend/src/api/{types.ts,client.ts}, frontend/src/styles/global.css, frontend/public/logos/*.svg (20 tickers + _placeholder)
  - Backend tests+skeletons: backend/app/cache.py, backend/tests/test_cache.py, backend/app/yfinance_client.py, backend/tests/test_yfinance_client.py, backend/app/routers/stocks.py, backend/app/main.py, backend/tests/test_stocks_router.py
  - Frontend hooks: frontend/src/api/queries.ts, frontend/src/api/queries.test.tsx
  - Frontend components (each with .tsx/.props.ts/.test.tsx/.css): shared/{CompanyIcon,ChangeIndicator,ErrorBadge}, leaderboard/{StockRow,StockTable}, stock-detail/{StockHeader,PriceTicker,StockChart}, layout/AppShell, dashboard/{MarketSummaryCard,SectorBreakdownCard,TopMoversCard}, providers/AppProviders, pages/{LeaderboardPage,StockDetailPage,DashboardPage}
  - Frontend wiring: frontend/src/router.tsx, frontend/src/main.tsx
  - v2 delta: frontend/src/components/stock-detail/RawDataTable/{RawDataTable.tsx,.props.ts,.test.tsx,.css}; StockDetailPage.test.tsx extended (RawDataTable mocked + composition/order assertions) pre-freeze
- implementation files: backend/app/{cache.py,yfinance_client.py,routers/stocks.py} (bodies implemented); frontend/src/api/queries.ts; frontend implementations of CompanyIcon, ChangeIndicator, ErrorBadge, StockRow (+css fix), StockTable, StockHeader, PriceTicker, StockChart, RawDataTable, LeaderboardPage, StockDetailPage
- red verification (2026-08-19): backend pytest 26 failed / 1 passed (CORS preflight — middleware is wired plumbing); frontend vitest 49 failed / 26 passed (passes are deliberately-implemented plumbing/placeholders: AppProviders, AppShell, dashboard cards, DashboardPage, query-key constants). All 75 failures confirmed to be "not implemented" errors — none from import/syntax/setup.

## Plan (v1, 2026-08-19)

### Context
Empty target directory `/Users/hugo.frausin45/Accelerator/self-learning/NewLangs/MMM` — from-scratch local learning project. Local TS/React app showing live-ish data for 20 UK-listed companies via `yfinance`: a leaderboard home page, a per-stock detail page with a live time-series chart (minimal re-renders on 20s poll), and a placeholder market dashboard page. Runnable both via plain local commands and via `docker compose`.

### Architecture
Two services, run as two local processes or via `docker compose` (same published ports either way — backend 8000, frontend 5173):
- `backend/` — Python FastAPI + `yfinance`. Fixed universe of 20 UK tickers (a ticker is the short symbol identifying a stock on an exchange, e.g. `AZN.L` = AstraZeneca on the London Stock Exchange, the `.L` suffix being Yahoo Finance's LSE convention). In-memory TTL cache (`cachetools.TTLCache`, ~15-20s) + per-key `asyncio.Lock` (thundering-herd protection). All `yfinance` calls isolated in `yfinance_client.py` with per-ticker try/except.
- `frontend/` — Vite + React + TS, `react-router-dom` (3 routes), `@tanstack/react-query` (`refetchInterval: 20000`, ticker-scoped query keys, `select` slicing), `recharts` for the detail chart. Static local SVG logos in `public/logos/`.
- `docker-compose.yml` + Dockerfile per service at repo root.

**Standing frontend conventions (durable, not project-specific — see memory `feedback-fe-component-structure`):**
- Every component lives in its own folder with four co-located files: `Component.tsx`, `Component.props.ts` (prop types), `Component.test.tsx`, `Component.css`.
- Page components (`pages/*`) stay minimal — thin composition/layout only, no business logic or data-fetching state held at the page level; that lives in child components/hooks.
- One slim, root-level global context provider (`providers/AppProviders.tsx`) composes cross-cutting concerns (e.g. `QueryClientProvider`) instead of scattering multiple ad hoc providers or prop-drilling — kept minimal, not a dumping ground.

```
MMM/
├── docker-compose.yml
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py            # FastAPI app, CORS
│   │   ├── config.py          # cache TTL, allowed origins
│   │   ├── tickers.py         # fixed list of 20 (ticker, name, sector)
│   │   ├── schemas.py         # pydantic response models
│   │   ├── cache.py           # TTL cache + per-key lock
│   │   ├── yfinance_client.py # all yfinance calls, per-ticker error handling, GBp->GBP conversion
│   │   └── routers/stocks.py  # GET /api/stocks, /api/stocks/{ticker}, /api/stocks/{ticker}/history
│   └── tests/                 # pytest, yfinance mocked
└── frontend/
    ├── Dockerfile
    ├── package.json / vite.config.ts / tsconfig.json
    ├── public/logos/{TICKER}.svg (+ _placeholder.svg)
    └── src/
        ├── main.tsx
        ├── router.tsx
        ├── providers/AppProviders/{AppProviders.tsx,.props.ts,.test.tsx}
        ├── api/{client.ts, types.ts, queries.ts, queries.test.ts}
        ├── pages/
        │   ├── LeaderboardPage/{LeaderboardPage.tsx,.props.ts,.test.tsx,.css}
        │   ├── StockDetailPage/{...}
        │   └── DashboardPage/{...}
        └── components/
            ├── layout/AppShell/{AppShell.tsx,.props.ts,.test.tsx,.css}
            ├── leaderboard/StockTable/{...}, StockRow/{...}
            ├── stock-detail/StockHeader/{...}, PriceTicker/{...}, StockChart/{...}
            ├── dashboard/MarketSummaryCard/{...}, SectorBreakdownCard/{...}, TopMoversCard/{...}  # placeholders only
            └── shared/CompanyIcon/{...}, ChangeIndicator/{...}, ErrorBadge/{...}
```

### The 20 companies
Dropped Sainsbury's (SBRY.L) from the natural 21-name cross-sector FTSE set. Final list (ticker — name — sector): AZN.L AstraZeneca (Pharma), GSK.L GSK (Pharma), ULVR.L Unilever (Consumer Goods), RKT.L Reckitt Benckiser (Consumer Goods), DGE.L Diageo (Beverages), BATS.L British American Tobacco (Tobacco), SHEL.L Shell (Energy), BP.L BP (Energy), RIO.L Rio Tinto (Mining), GLEN.L Glencore (Mining), HSBA.L HSBC (Banking), BARC.L Barclays (Banking), LLOY.L Lloyds (Banking), PRU.L Prudential (Insurance), AV.L Aviva (Insurance), VOD.L Vodafone (Telecom), BT-A.L BT Group (Telecom), TSCO.L Tesco (Retail), NG.L National Grid (Utilities), RR.L Rolls-Royce (Aerospace). `BT-A.L`/`AV.L` symbols to be spot-checked against live `yfinance` during implementation.

### Data flow
1. On `GET /api/stocks`, cache checked by TTL; on miss, one batched `yf.Tickers(...)` call fetches `fast_info` for all 20 (lock-protected), GBp→GBP converted, % change vs `previous_close`. Per-ticker failures return `is_stale: true` (serving last cached value if available) rather than failing the batch.
2. `GET /api/stocks/{ticker}/history` fetches `history(period="1d", interval="5m")`, falling back to `period="5d", interval="15m"` if empty; cached independently per ticker.
3. Frontend polls `['stocks']` every 20s for the leaderboard, and independently polls `['stock', ticker]` (price/change) and `['stock', ticker, 'history']` (chart points) on the detail page — separate query keys so a price tick never touches chart render inputs. `StockChart` merges new points into local state by append+dedupe (by timestamp), not full replace, avoiding remount/redraw. `StockRow` is `React.memo`'d; structural sharing keeps unchanged rows' references stable.
4. `DashboardPage` makes no network calls — static placeholders only.

### Error handling
- Backend per-ticker: `yfinance_client.py` catches exceptions/empty frames per ticker; returns an error result instead of raising.
- Backend response-level: prefer stale cached value (`is_stale: true`) over `null` on fresh-fetch failure; 502/503 only when no cache exists at all and fetch fails. `/api/stocks/{ticker}` 404s for tickers outside the fixed 20. CORS restricted to the configured frontend origin, GET-only.
- Frontend: per-row `ErrorBadge`/greyed price for `is_stale`/`error` rows without blocking other rows. Whole-page network failure (backend unreachable) surfaces as a page-level banner via React Query's error state, distinct from per-row staleness.

### Tests to write (Stage 2 target — all against mocked `yfinance` / mocked API, no live network calls in tests)

**Backend — `cache.py`**
- Cached value served within TTL without re-invoking the fetch function.
- Cache expires after TTL and triggers a recompute.
- Per-key lock collapses concurrent cache-miss callers into a single underlying fetch.

**Backend — `yfinance_client.py`**
- Successful fetch: price/previous_close/change/change_percent computed correctly from mocked `fast_info`.
- GBp→GBP conversion applied correctly (divide by 100).
- Exception from `yfinance` → returns an error result, does not raise.
- Missing/`None` `fast_info` fields → treated as an error/stale result, not a crash.
- History: default `1d/5m` maps a mocked DataFrame to `points` correctly.
- History: empty `1d/5m` frame falls back to `5d/15m` and returns those points instead.
- History: fetch failure with no cache available → error result with empty points.

**Backend — `routers/stocks.py`** (FastAPI `TestClient`, `yfinance_client` mocked)
- `GET /api/stocks` → 200 with all 20 tickers when all succeed.
- `GET /api/stocks` → 200 even when some tickers fail (partial failure never 500s; failed entries carry `is_stale`/`error`).
- `GET /api/stocks/{ticker}` valid → 200 with expected shape; unknown ticker → 404.
- `GET /api/stocks/{ticker}/history` valid → 200 with points; on fetch failure with cached data present → 200 with `is_stale: true`; on fetch failure with no cache at all → 503.
- CORS header present for the allowed origin.

**Frontend — `api/queries.ts`**
- `useStocksQuery` parses the mocked API response into the expected TS shape; configured with `refetchInterval: 20000`.
- `useStockDetailQuery`/`useStockHistoryQuery` use ticker-scoped keys; data for two different tickers remains independent in the cache.

**Frontend — `StockRow`**
- Renders name, icon, price, and correct up/down styling based on `change_percent` sign.
- Does not re-render when its own row data is unchanged across a parent re-render (memoization check via render-count spy).

**Frontend — `CompanyIcon`**
- Renders the correct logo `src` for a known ticker; falls back to the placeholder icon on image load error.

**Frontend — `ChangeIndicator`**
- Positive/negative/zero `change_percent` render the correct arrow + color/neutral state.

**Frontend — `StockChart`**
- New history points are appended/deduped (by timestamp) into existing local state rather than replacing the array wholesale.
- Does not remount (mount-count stays at 1) across successive polls while `ticker` stays the same.

**Frontend — `PriceTicker`**
- Renders independently of chart/history data; does not re-render when only the history query updates.

**Frontend — `DashboardPage`**
- Renders placeholder cards and triggers no network requests.

### Local run
- Plain terminals: `cd backend && uv run uvicorn app.main:app --reload --port 8000` / `cd frontend && npm run dev`.
- Docker Compose: `docker compose up` builds/runs both (backend: Python slim + `uv`, `uvicorn --host 0.0.0.0 --reload`; frontend: Node LTS, `vite --host 0.0.0.0`), same 8000/5173 host ports as plain-terminal mode.

### Other confirmed decisions
Backend: Python + FastAPI + `yfinance`. Frontend: Vite + TS + React, `react-router-dom`, TanStack Query 20s polling, Recharts. Icons: local static SVGs + placeholder fallback. Change indicator vs **previous close**. Prices shown converted to pounds (e.g. "£112.34").

### Verification (end of Stage 3)
- Backend: `pytest` green against mocked `yfinance`; manual one-off smoke check against real `yfinance` to confirm `fast_info`/`history()` shapes match mocks, spot-check `BT-A.L`/`AV.L` symbols and 5m intraday quality.
- Frontend: `vitest` green; manual browser check of all 3 routes — leaderboard updates ~20s, detail chart appends without full redraw/remount, dashboard is static with no network activity.
- Full stack: `docker compose up` from a clean checkout reaches a working app at `localhost:5173` with no manual steps beyond that command.

### UI layout (v2, from user wireframe)
- All pages: top header bar with a left menu/icon slot ("[...]") and centered title (AppShell).
- Leaderboard: each row is a 3-column pill layout — stock name (+icon) | stock price | stats (change indicator).
- Stock detail: large graph card (price vs time) on top; below it a **RawDataTable** card listing the history points (time, close price) — new component `stock-detail/RawDataTable` (4-file convention), consumes `useStockHistoryQuery(ticker)` (same query key as the chart, so no extra network fetch).
- Dashboard: two placeholder cards side by side on top (MarketSummaryCard, SectorBreakdownCard), one full-width card below (TopMoversCard).

## Changelog
- v3.5 (2026-08-20): restored 11 backend router tests (TestTotalFailureServesStale + TestRateLimitBackoff, _reset_caches backoff reset, fake_clock fixture) lost to an out-of-band overwrite of test_stocks_router.py at 11:05 (stale editor buffer suspected; test_cache.py content verified intact despite matching mtime). Suite back to 44 passed; restored tests mutation-checked as load-bearing. Fresh test-file hash snapshot taken (test-hashes-v3-restored.txt in job tmp).
- v3.4 (2026-08-20): committed to the light theme — removed the prefers-color-scheme:dark token block and pinned color-scheme:light (user's screenshot showed OS dark mode overriding the pastel-yellow/paper-white brand with the navy/near-black palette; the layout itself was structurally fine, further washed out by the whole-board stale fade while the backend was rate-limited).
- v3.3 (2026-08-20): header bar recolored navy -> pastel yellow (--pastel #f7e9b8), page ground -> paper white (#fdfcf7, cards pure white); bar ink flipped to navy in light mode via --color-bar-text/-muted tokens (dark mode keeps deep-navy bar with light ink); M3 mark now inks in bar-text color for contrast on both themes.
- v3.2 (2026-08-20): header refinements per user screenshot feedback — M³ mark moved to the bar's top-right slot; header is now sticky and condenses on scroll (title fades/hides, mark glides to centre, bar tightens with a lift shadow); superscript 3 fixed to sit at the M's shoulder (was rendering baseline-right). AppShell test gains a scroll-condense case.
- v3.1 (2026-08-20): user rebrand — app renamed "Mario's Money Makers" with an M³ logo mark (masthead plate in AppShell + public/logos/m3-logo.svg favicon), palette shifted to pastel-yellow hues (light: #faf3d9 ledger stock, warmed surfaces/borders/muted inks; dark mode warm-tinted to match; navy/brass/racing-green/claret retained). AppShell title test retargeted accordingly.
- v1 (2026-08-19): initial plan, approved via ExitPlanMode; frontend component-structure/minimal-page/slim-provider conventions folded in per user's standing preference before Stage 2 began.
- v2 (2026-08-19): user approved Stage 2 tests and supplied a wireframe; added UI layout section and new RawDataTable component on the detail page (minor rescope — Stage 2 delta for that slice only, then Stage 3).
- v3 (2026-08-19): live testing hit Yahoo IP-level 429s (yfinance 1.6.0's fast_info fires ~2 HTTP requests/ticker → 40+ per 20s refresh) plus a bug where an all-error batch overwrote the stale store (blank page). User directed: (backend) minimize call count via batched/leaner requests and exponential backoff, serve-stale-on-total-failure, longer TTLs — authorizes amending the frozen yfinance-client/router test mocks pinned to the old call shape; (frontend) replace verbose error text with greyed-out rows for stocks without fresh data and a single "lost data communication with the backend" banner, plus a UI polish pass via the frontend-design skill — authorizes amending affected frontend tests to the new UX. Both suites must end green; all test amendments reported back for review.
- v3 outcome (2026-08-19): backend — serve-stale-on-total-failure (failed batches never cached), fetch_summaries restructured to one history(5d,1d) call per ticker (~40→20 req/refresh; currency read zero-request from yfinance private `_price_history._history_metadata` with assume-GBP fallback — fragile across yfinance upgrades, needs live smoke check), exponential backoff (base=TTL, cap 600s, injectable clock), TTLs 90/120s; 6 tests adapted to new call shape (assertions preserved), 17 added → 44/44. Frontend — "The London Board" design (LSE navy/brass/ledger-paper, Gill Sans display + mono tabular numerals, racing green/claret up-down, 3px direction spine, dark-mode tokens); error UX: ErrorBadge → hollow stale dot (never shows error strings), stale rows greyed via --stale-opacity keeping last price, ConnectionBanner ("We have lost data communication with the backend.", role=status) with last-data-kept on query error, detail page is-disconnected treatment; error-UX tests rewritten equivalently, 28 added → 108/108. Open items: live smoke test (currency/private-attr + last-daily-bar assumptions); history endpoint not behind backoff; greying asserted by class not rendered style; detail-page banner keyed off detail query only.
- Stage 3 outcome (2026-08-19): green in cycle 1 — backend 27/27 pytest, frontend 80/80 vitest; all 21 frozen test-file hashes verified unchanged after implementation. Open TEST CONCERN (implementer, not acted on): `npx tsc -b` fails with one type error inside frozen `src/api/queries.test.tsx:153` — `Query.options` is typed as core `QueryOptions`, which lacks `refetchInterval` (runtime value exists; vitest passes). Fix options for user: cast in the test, or exclude `*.test.tsx` from the production build tsconfig.
