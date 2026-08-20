import type { StockSummary } from '../../../api/types'

export interface StockTableProps {
  stocks: StockSummary[]
  /**
   * True when the whole board is showing last known figures — i.e. the
   * leaderboard query itself is failing. Greys the entire table the same
   * way a single stale stock greys its own row.
   */
  isDegraded?: boolean
}
