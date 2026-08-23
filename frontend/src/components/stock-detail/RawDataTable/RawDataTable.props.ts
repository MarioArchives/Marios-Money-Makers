import type { HistoryRange } from '../../../api/types'

export interface RawDataTableProps {
  ticker: string
  /** Picks the default stored tier: 1d -> minute, 30d -> hour, all -> daily. */
  range?: HistoryRange
}
