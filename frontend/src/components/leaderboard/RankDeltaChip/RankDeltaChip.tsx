import type { RankDeltaChipProps } from './RankDeltaChip.props'
import './RankDeltaChip.css'

/**
 * Tiny "▲3"/"▼2" chip shown briefly after a re-rank. `aria-hidden`: purely
 * decorative, the new position is the real information. Unmounted by the
 * owning `StockTable` after `RANK_DELTA_MS`; fade timed by `--motion-rank-delta`.
 */
export function RankDeltaChip({ delta = 0 }: RankDeltaChipProps): JSX.Element | null {
  if (delta === 0) {
    return null
  }
  const up = delta > 0
  return (
    <span
      className={`rank-delta rank-delta--${up ? 'up' : 'down'} numeral`}
      data-testid="rank-delta"
      aria-hidden="true"
    >
      {up ? '▲' : '▼'}
      {Math.abs(delta)}
    </span>
  )
}
