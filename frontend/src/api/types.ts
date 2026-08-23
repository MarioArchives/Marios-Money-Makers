export interface StockSummary {
  ticker: string
  name: string
  sector: string
  price: number | null
  currency: string
  previous_close: number | null
  change: number | null
  change_percent: number | null
  is_stale: boolean
  error: string | null
}

export interface StocksResponse {
  updated_at: string
  stocks: StockSummary[]
}

/** Backend `?range=` values; `all` is every daily bar ever stored for the ticker (never pruned), so it grows over time. */
export const HISTORY_RANGES = ['1d', '30d', 'all'] as const
export type HistoryRange = (typeof HISTORY_RANGES)[number]
export const DEFAULT_HISTORY_RANGE: HistoryRange = '1d'

export function isHistoryRange(value: unknown): value is HistoryRange {
  return typeof value === 'string' && (HISTORY_RANGES as readonly string[]).includes(value)
}

export interface HistoryPoint {
  t: string
  close: number
}

export interface HistoryResponse {
  ticker: string
  interval: string
  range: string
  points: HistoryPoint[]
  is_stale: boolean
  error: string | null
}

/** SQLite tiers behind the stored-data endpoint; table names are `bars_<tier>`, `days` is the never-pruned long-term tier. */
export const STORED_TIERS = ['minute', 'hour', 'days'] as const
export type StoredTier = (typeof STORED_TIERS)[number]

/** Which stored tier backs each history range (same mapping as the backend). */
export const TIER_FOR_RANGE: Record<HistoryRange, StoredTier> = {
  '1d': 'minute',
  '30d': 'hour',
  all: 'days',
}

/** One `bars_<tier>` row exactly as stored; `analytics` is the raw Alpaca bar object. */
export interface StoredBar {
  ts: string
  price: number
  analytics: Record<string, unknown>
  recorded_at: string
}

export interface StoredDataResponse {
  ticker: string
  tier: StoredTier
  table: string
  currency: string
  last_fetch_at: string | null
  counts: Record<StoredTier, number>
  rows: StoredBar[]
}

/** A USD->GBP reference rate (see `api/fx.ts`). */
export interface FxRate {
  base: 'USD'
  quote: 'GBP'
  /** GBP per 1 USD. */
  rate: number
  /** Rate date as published (YYYY-MM-DD). */
  date: string
  /** Human label of where the rate came from. */
  source: string
}

/** Alpaca's market clock as cached by the backend (timestamps `YYYY-MM-DDTHH:MM:SSZ`); `is_stale`/`error` follow the same convention as every other response. */
export interface MarketClockResponse {
  timestamp: string
  is_open: boolean
  next_open: string
  next_close: string
  fetched_at: string
  is_stale: boolean
  error: string | null
}
