import type { RangeSelectorProps } from './RangeSelector.props'
import { HISTORY_RANGES, type HistoryRange } from '../../../api/types'
import './RangeSelector.css'

/** Human labels for the backend `?range=` values. */
export const RANGE_LABELS: Record<HistoryRange, string> = {
  '1d': '1D',
  '30d': '30D',
  all: 'ALL',
}

/**
 * Segmented control for the stock detail page's history range. Purely
 * presentational and controlled: renders one button per backend range,
 * marks the selected one with `aria-pressed`, and reports clicks through
 * `onChange`. Where the selection lives (URL, state) is the caller's
 * business — this component never touches the router or any query.
 */
export function RangeSelector({ value, onChange }: RangeSelectorProps): JSX.Element {
  return (
    <div className="range-selector" role="group" aria-label="History range" data-testid="range-selector">
      {HISTORY_RANGES.map((range) => {
        const selected = range === value
        return (
          <button
            key={range}
            type="button"
            className={`range-selector__option${selected ? ' is-selected' : ''}`}
            aria-pressed={selected}
            onClick={() => {
              if (!selected) onChange(range)
            }}
          >
            {RANGE_LABELS[range]}
          </button>
        )
      })}
    </div>
  )
}
