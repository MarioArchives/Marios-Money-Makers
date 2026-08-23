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

/** Polling interval (ms) used for every stock-related query, per plan: 20s. */
export const POLL_INTERVAL_MS = 20000

/**
 * Ms until the next wall-clock multiple of `intervalMs`, in (0, intervalMs]; queries share this tick (not "N ms after my own last fetch") so differently-mounted queries refresh together.
 */
export function msUntilNextPoll(
  now: number = Date.now(),
  intervalMs: number = POLL_INTERVAL_MS,
): number {
  return intervalMs - (now % intervalMs)
}

/** `refetchInterval` for the polled stock queries: React Query re-evaluates this after every fetch, landing each refetch on the next shared tick. */
export function alignedPollInterval(): number {
  return msUntilNextPoll()
}

/** staleTime (ms): kept just under POLL_INTERVAL_MS so a scheduled refetch is always considered "due". */
export const STALE_TIME_MS = 18000

/** Query key for the leaderboard (`GET /api/stocks`). */
export const stocksKey = ['stocks'] as const

/** Ticker-scoped query key for a single stock's summary (`GET /api/stocks/{ticker}`). */
export function stockKey(ticker: string): readonly ['stock', string] {
  return ['stock', ticker] as const
}

/** Ticker- and range-scoped query key for a single stock's history; each range is its own cache entry so switching 1d -> 30d never overwrites the intraday series. */
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
 * Polls `GET /api/stocks/{ticker}` for a single stock's price/change. `select` narrows the subscription; pass a module-level function so React Query's structural sharing keeps a stable reference and skips re-renders.
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

/** Polls a single stock's chart points at the requested range (`1d` minute bars, `30d` hourly, `all` every stored daily bar), independently of the detail query. */
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

/** Ticker- and tier-scoped query key for the raw SQLite inspection endpoint; nested under the stock key, separate from `history` so the two never share a cache entry. */
export function storedKey(
  ticker: string,
  tier: StoredTier,
): readonly ['stock', string, 'stored', StoredTier] {
  return ['stock', ticker, 'stored', tier] as const
}

/** Polls everything the backend's SQLite store holds for one ticker in one tier (every row, every column); backs the raw-data table. */
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

/** Whether the US market is open and the next open/close instants. Keyed under `market`, not `stocks`/`stock`, so `usePollCountdown` (header countdown) ignores it; its consumer is `MarketStatusBanner`. */
export function useMarketClockQuery(): UseQueryResult<MarketClockResponse, Error> {
  return useQuery({
    queryKey: marketClockKey,
    queryFn: () => apiGet<MarketClockResponse>('/api/market/clock'),
    refetchInterval: alignedPollInterval,
    staleTime: STALE_TIME_MS,
  })
}
