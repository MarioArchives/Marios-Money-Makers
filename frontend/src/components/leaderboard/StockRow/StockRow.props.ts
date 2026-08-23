import type { StockSummary } from '../../../api/types'

export interface StockRowProps {
  stock: StockSummary
  /** Rank change since the last re-rank: positive = rose, negative = fell.
   * Owned by `StockTable`, cleared after `RANK_DELTA_MS`. */
  rankDelta?: number
}
