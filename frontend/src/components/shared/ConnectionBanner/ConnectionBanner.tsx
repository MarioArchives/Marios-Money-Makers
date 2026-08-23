import type { ConnectionBannerProps } from './ConnectionBanner.props'
import './ConnectionBanner.css'

/**
 * Page-level message when the backend is unreachable. Deliberately calm:
 * `role="status"` not `role="alert"`, one sentence, no colour alarm or
 * technical detail — everything already on screen just stays greyed.
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
