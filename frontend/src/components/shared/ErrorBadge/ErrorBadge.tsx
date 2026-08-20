import type { ErrorBadgeProps } from './ErrorBadge.props'
import './ErrorBadge.css'

/**
 * The marker for a stock that is showing its last known figures rather than
 * fresh ones.
 *
 * Retained at its original path/name from the pre-v3 error UX, but reduced
 * to a single muted dot: the row itself carries the message by greying out,
 * and a rate-limited feed is not something the reader can act on, so no
 * per-row error text is rendered. Backend error strings are deliberately
 * never shown here — not as text, not in the tooltip.
 */
const STALE_LABEL = 'Showing last known price'

export function ErrorBadge({ isStale, error }: ErrorBadgeProps): JSX.Element | null {
  const isDegraded = isStale || Boolean(error)

  if (!isDegraded) {
    return null
  }

  return (
    <span
      className="stale-dot"
      data-testid="stale-dot"
      role="img"
      aria-label={STALE_LABEL}
      title={STALE_LABEL}
    />
  )
}

export { STALE_LABEL }
