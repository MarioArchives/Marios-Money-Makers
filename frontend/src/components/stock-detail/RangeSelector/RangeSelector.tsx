import { memo } from 'react'
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
 * Segmented control for the history range: purely presentational and
 * controlled, reports clicks via `onChange`. Never touches the router or a
 * query itself — where the selection lives is the caller's business.
 */
function RangeSelectorComponent({ value, onChange }: RangeSelectorProps): JSX.Element {
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

/** Memoised: `onChange` from `useHistoryRange` is stable, so only a range change re-renders it. */
export const RangeSelector = memo(RangeSelectorComponent)
