import type { RankDeltaChipProps } from './RankDeltaChip.props'
import './RankDeltaChip.css'

/**
 * Tiny "▲3" / "▼2" chip that sits after a row's ticker for a moment after
 * the board re-ranks, so the size of a move registers even if the slide
 * was missed. Purely decorative — the new position *is* the information
 * — so it is hidden from assistive tech. Its pop-in/fade-out is a CSS
 * animation timed by `--motion-rank-delta`; the owner unmounts it when
 * that window ends (`RANK_DELTA_MS` in `StockTable`).
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
