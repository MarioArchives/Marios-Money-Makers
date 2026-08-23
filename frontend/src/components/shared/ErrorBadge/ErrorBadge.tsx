import type { ErrorBadgeProps } from './ErrorBadge.props'
import './ErrorBadge.css'

/**
 * Marker for a stock showing last known figures rather than fresh ones.
 * A single muted dot only — backend error strings are deliberately never
 * shown here, not as text, not in the tooltip.
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
