import type { HistoryRange } from '../../../api/types'

export interface StockChartProps {
  ticker: string
  /** History range to plot; defaults to the intraday (`1d`) series. */
  range?: HistoryRange
}
