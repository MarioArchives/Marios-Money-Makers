import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { apiGet } from './client'
import {
  DEFAULT_HISTORY_RANGE,
  type HistoryRange,
  type HistoryResponse,
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
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
  })
}

/**
 * Polls `GET /api/stocks/{ticker}` every POLL_INTERVAL_MS for a single
 * stock's price/change, independently of that ticker's history query.
 */
export function useStockDetailQuery(ticker: string): UseQueryResult<StockSummary, Error> {
  return useQuery({
    queryKey: stockKey(ticker),
    queryFn: () => apiGet<StockSummary>(`/api/stocks/${ticker}`),
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
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
    refetchInterval: POLL_INTERVAL_MS,
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
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
  })
}
