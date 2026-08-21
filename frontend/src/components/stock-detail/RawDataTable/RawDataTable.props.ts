import type { HistoryRange } from '../../../api/types'

export interface RawDataTableProps {
  ticker: string
  /**
   * Current history range; picks the stored tier shown by default
   * (1d -> minute, 30d -> hour, all -> daily). The user can still switch
   * tiers inside the card.
   */
  range?: HistoryRange
}
