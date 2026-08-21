import type { FxRateNoteProps } from './FxRateNote.props'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import './FxRateNote.css'

export const FX_UNAVAILABLE_MESSAGE = 'GBP rate unavailable'

/**
 * One-line disclosure of the rate behind every "≈ £…" figure on the page:
 * "1 USD = 0.7339 GBP · ECB, 2026-08-20". Renders nothing while the rate
 * is still loading, and a muted "unavailable" note if it failed — the
 * GBP figures themselves disappear in that case, so the note is the only
 * signal.
 */
export function FxRateNote(_props: FxRateNoteProps): JSX.Element | null {
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
