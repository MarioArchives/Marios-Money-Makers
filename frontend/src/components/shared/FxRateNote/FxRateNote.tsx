import { memo } from 'react'
import type { FxRateNoteProps } from './FxRateNote.props'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import './FxRateNote.css'

export const FX_UNAVAILABLE_MESSAGE = 'GBP rate unavailable'

/**
 * One-line disclosure of the FX rate behind every "≈ £…" figure, e.g.
 * "1 USD = 0.7339 GBP · ECB, 2026-08-20". Renders nothing while loading.
 * Memoised: re-renders only on the shared rate context, not the page's poll.
 */
function FxRateNoteComponent(_props: FxRateNoteProps): JSX.Element | null {
  void _props
  const { status, rate } = useFxRate()
  if (status === 'loading') {
    return null
  }
  if (!rate) {
    return (
      <span className="fx-rate-note is-unavailable" data-testid="fx-rate-note">
        {FX_UNAVAILABLE_MESSAGE}
      </span>
    )
  }
  return (
    <span className="fx-rate-note" data-testid="fx-rate-note">
      1 {rate.base} = {rate.rate.toFixed(4)} {rate.quote} · {rate.source}, {rate.date}
    </span>
  )
}

export const FxRateNote = memo(FxRateNoteComponent)
