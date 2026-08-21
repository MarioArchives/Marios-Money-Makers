import type { StockSummary } from '../../../api/types'

export interface StockRowProps {
  stock: StockSummary
  /**
   * Places this row moved in the last re-rank (positive = up, negative =
   * down), shown as a transient chip after the ticker. Omit/0 for none.
   * Owned by `StockTable`, which also clears it after `RANK_DELTA_MS`.
   */
  rankDelta?: number
}
