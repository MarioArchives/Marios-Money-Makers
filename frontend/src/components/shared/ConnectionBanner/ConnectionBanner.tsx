import type { ConnectionBannerProps } from './ConnectionBanner.props'
import './ConnectionBanner.css'

/**
 * The single page-level message shown when the backend is unreachable.
 *
 * Deliberately calm: `role="status"` rather than `role="alert"`, one
 * sentence, no colour alarm and no technical detail. Everything already on
 * screen stays on screen (greyed) while this is up, so the banner's only
 * job is to explain why the figures have stopped moving.
 */
export const CONNECTION_LOST_MESSAGE = 'We have lost data communication with the backend.'

export function ConnectionBanner(_props: ConnectionBannerProps): JSX.Element {
  void _props
  return (
    <p className="connection-banner" role="status" data-testid="connection-banner">
      <span className="connection-banner__mark" aria-hidden="true" />
      {CONNECTION_LOST_MESSAGE}
    </p>
  )
}
