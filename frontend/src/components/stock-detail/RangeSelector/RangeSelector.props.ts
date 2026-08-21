import type { HistoryRange } from '../../../api/types'

export interface RangeSelectorProps {
  /** Currently selected history range. */
  value: HistoryRange
  /** Called with the newly selected range when the user picks another one. */
  onChange: (range: HistoryRange) => void
}
