import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { apiGet } from './client'
import {
  DEFAULT_HISTORY_RANGE,
  type HistoryRange,
  type HistoryResponse,
  type MarketClockResponse,
  type StockSummary,
  type StocksResponse,
  type StoredDataResponse,
  type StoredTier,
} from './types'

/**
 * Polling interval (ms) used for every stock-related query, per plan: 20s.
 */
export const POLL_INTERVAL_MS = 20000

/**
 * Milliseconds from `now` until the next wall-clock multiple of
 * `intervalMs` — always in (0, intervalMs]. Every polled stock query
 * schedules its refetch on this *shared* tick rather than "N ms after my
 * own last fetch", so queries that mount at different moments (switching
 * 1d -> 30d -> all on the stock page, a cached range remounting, the
 * detail/chart/table trio) all refresh together. That gives the header
 * countdown (`usePollCountdown`) exactly one cycle to show instead of a
 * ragged set of per-query timers jumping it to partial fills.
 */
export function msUntilNextPoll(
  now: number = Date.now(),
  intervalMs: number = POLL_INTERVAL_MS,
): number {
  return intervalMs - (now % intervalMs)
}

/**
 * `refetchInterval` for the polled stock queries. React Query re-evaluates
 * a function interval after every fetch (and restarts its timer from that
 * moment), so each refetch lands on the next shared tick.
 */
export function alignedPollInterval(): number {
  return msUntilNextPoll()
}

/**
 * staleTime (ms) used for every stock-related query. Kept just under
 * POLL_INTERVAL_MS so a scheduled refetch is always considered "due".
 */
export const STALE_TIME_MS = 18000

/** Query key for the leaderboard (`GET /api/stocks`). */
export const stocksKey = ['stocks'] as const

/** Ticker-scoped query key for a single stock's summary (`GET /api/stocks/{ticker}`). */
export function stockKey(ticker: string): readonly ['stock', string] {
  return ['stock', ticker] as const
}

/**
 * Ticker- and range-scoped query key for a single stock's history
 * (`GET /api/stocks/{ticker}/history?range=...`). Each range is its own
 * cache entry so switching 1d -> 30d never overwrites the intraday series.
 */
export function historyKey(
  ticker: string,
  range: HistoryRange = DEFAULT_HISTORY_RANGE,
): readonly ['stock', string, 'history', HistoryRange] {
  return ['stock', ticker, 'history', range] as const
}

/**
 * Polls `GET /api/stocks` every POLL_INTERVAL_MS for the leaderboard.
 */
export function useStocksQuery(): UseQueryResult<StocksResponse, Error> {
  return useQuery({
    queryKey: stocksKey,
    queryFn: () => apiGet<StocksResponse>('/api/stocks'),
    refetchInterval: alignedPollInterval,
    staleTime: STALE_TIME_MS,
  })
}

/**
 * Polls `GET /api/stocks/{ticker}` every POLL_INTERVAL_MS for a single
 * stock's price/change, independently of that ticker's history query.
 *
 * `select` narrows what the caller subscribes to. React Query applies
 * structural sharing to the selected value, so a component that only
 * needs e.g. `name`/`sector` keeps a stable reference across polls and
 * does not re-render on every price tick (pass a module-level function so
 * the selector itself is stable). Observers with and without `select`
 * share the same cache entry — no extra requests.
 */
export function useStockDetailQuery(ticker: string): UseQueryResult<StockSummary, Error>
export function useStockDetailQuery<T>(
  ticker: string,
  select: (summary: StockSummary) => T,
): UseQueryResult<T, Error>
export function useStockDetailQuery<T = StockSummary>(
  ticker: string,
  select?: (summary: StockSummary) => T,
): UseQueryResult<T, Error> {
  return useQuery<StockSummary, Error, T>({
    queryKey: stockKey(ticker),
    queryFn: () => apiGet<StockSummary>(`/api/stocks/${ticker}`),
    refetchInterval: alignedPollInterval,
    staleTime: STALE_TIME_MS,
    select,
  })
}

/**
 * Polls `GET /api/stocks/{ticker}/history?range=...` every POLL_INTERVAL_MS
 * for a single stock's chart points at the requested range (`1d` minute
 * bars, `30d` hourly, `all` every stored daily bar), independently of that
 * ticker's detail query.
 */
export function useStockHistoryQuery(
  ticker: string,
  range: HistoryRange = DEFAULT_HISTORY_RANGE,
): UseQueryResult<HistoryResponse, Error> {
  return useQuery({
    queryKey: historyKey(ticker, range),
    queryFn: () => apiGet<HistoryResponse>(`/api/stocks/${ticker}/history?range=${range}`),
    refetchInterval: alignedPollInterval,
    staleTime: STALE_TIME_MS,
  })
}

/**
 * Ticker- and tier-scoped query key for the raw SQLite inspection endpoint
 * (`GET /api/stocks/{ticker}/stored?tier=...`). Nested under the stock key,
 * separate from `history` so the two never share a cache entry.
 */
export function storedKey(
  ticker: string,
  tier: StoredTier,
): readonly ['stock', string, 'stored', StoredTier] {
  return ['stock', ticker, 'stored', tier] as const
}

/**
 * Polls `GET /api/stocks/{ticker}/stored?tier=...` every POLL_INTERVAL_MS:
 * everything the backend's SQLite store holds for one ticker in one tier
 * (every row, every column). Backs the raw-data table.
 */
export function useStoredDataQuery(
  ticker: string,
  tier: StoredTier,
): UseQueryResult<StoredDataResponse, Error> {
  return useQuery({
    queryKey: storedKey(ticker, tier),
    queryFn: () => apiGet<StoredDataResponse>(`/api/stocks/${ticker}/stored?tier=${tier}`),
    refetchInterval: alignedPollInterval,
    staleTime: STALE_TIME_MS,
  })
}

/** Query key for the backend market clock (`GET /api/market/clock`). */
export const marketClockKey = ['market', 'clock'] as const

/**
 * Polls `GET /api/market/clock` on the shared aligned tick: whether the US
 * market is open right now, and the instants of the next open/close, as
 * reported by the backend (sourced from Alpaca's `/v2/clock`). Keyed under
 * `market` rather than `stocks`/`stock`, so `usePollCountdown` — which only
 * tracks stock queries for the header's "next refresh" clock — ignores it;
 * that is intended, this query has its own consumer (`MarketStatusBanner`).
 */
export function useMarketClockQuery(): UseQueryResult<MarketClockResponse, Error> {
  return useQuery({
    queryKey: marketClockKey,
    queryFn: () => apiGet<MarketClockResponse>('/api/market/clock'),
    refetchInterval: alignedPollInterval,
    staleTime: STALE_TIME_MS,
  })
}
