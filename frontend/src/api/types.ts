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
