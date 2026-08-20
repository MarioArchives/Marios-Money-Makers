import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { apiGet } from './client'
import type { HistoryResponse, StockSummary, StocksResponse } from './types'

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

/** Ticker-scoped query key for a single stock's history (`GET /api/stocks/{ticker}/history`). */
export function historyKey(ticker: string): readonly ['stock', string, 'history'] {
  return ['stock', ticker, 'history'] as const
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
 * Polls `GET /api/stocks/{ticker}/history` every POLL_INTERVAL_MS for a
 * single stock's chart points, independently of that ticker's detail query.
 */
export function useStockHistoryQuery(ticker: string): UseQueryResult<HistoryResponse, Error> {
  return useQuery({
    queryKey: historyKey(ticker),
    queryFn: () => apiGet<HistoryResponse>(`/api/stocks/${ticker}/history`),
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: STALE_TIME_MS,
  })
}
