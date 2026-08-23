import type { StockSummary } from '../../../api/types'

export interface StockTableProps {
  stocks: StockSummary[]
  /** True when the leaderboard query itself is failing (not a single stale row). */
  isDegraded?: boolean
}
