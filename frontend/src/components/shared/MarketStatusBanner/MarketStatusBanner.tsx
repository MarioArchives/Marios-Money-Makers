import { useEffect, useRef, useState } from 'react'
import type { MarketStatusBannerProps } from './MarketStatusBanner.props'
import { useMarketClockQuery } from '../../../api/queries'
import {
  describeCountdown,
  formatCountdown,
  formatMarketClock,
  formatViewerClock,
  isEarlyClose,
} from '../../../utils/marketHours'
import './MarketStatusBanner.css'

/** How often the countdown re-reads the clock. */
export const MARKET_STATUS_TICK_MS = 1000

/**
 * One line above the leaderboard saying whether the US market is open and
 * how long until that changes: "Market closed · opens in 4h 17m 03s", or
 * "Market open · closes in 2h 05m 10s". Outside US hours the IEX feed
 * visibly stops moving; this is the calm explanation, a sibling of
 * `ConnectionBanner` rather than a warning.
 *
 * Data comes from the backend clock (`useMarketClockQuery`, polled on the
 * shared 20s tick); the countdown itself ticks locally once a second
 * against the fetched `next_open`/`next_close` (the interval is cleared on
 * unmount). While the query has no data yet — first load, or an error with
 * nothing cached — the banner renders nothing rather than guessing. A
 * `is_stale` clock is still shown (the backend's best last-known reading),
 * just marked with a quiet `is-stale` class.
 *
 * When the local countdown reaches zero, the backend clock we are showing
 * is known to be out of date (the boundary it was counting down to has
 * arrived); the banner asks the query to refetch exactly once per boundary
 * and keeps showing the stale state clamped at `0m 00s` until the new
 * clock arrives.
 *
 * Accessibility: `role="status"` so it is findable as the page's status
 * line, but with `aria-live="off"` — a region that changes every second
 * would have a screen reader talking over everything else. The accessible
 * name (`aria-label`) carries the same state with the countdown rounded to
 * minutes, so a reader landing on it hears "Market closed, opens in 4 hours
 * 17 minutes" rather than the ticking seconds.
 */
export function MarketStatusBanner(_props: MarketStatusBannerProps): JSX.Element | null {
  void _props
  const query = useMarketClockQuery()
  const [now, setNow] = useState(() => new Date())
  const refetchedFor = useRef<string | null>(null)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), MARKET_STATUS_TICK_MS)
    return () => window.clearInterval(timer)
  }, [])

  const data = query.data
  const isOpen = data?.is_open ?? false
  const boundary = data ? new Date(isOpen ? data.next_close : data.next_open) : null
  const boundaryIso = boundary ? boundary.toISOString() : null
  const remainingMs = boundary ? boundary.getTime() - now.getTime() : 0
  const boundaryPassed = boundary !== null && remainingMs <= 0

  useEffect(() => {
    if (boundaryPassed && boundaryIso !== null && refetchedFor.current !== boundaryIso) {
      refetchedFor.current = boundaryIso
      void query.refetch()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundaryIso, boundaryPassed])

  if (!data || !boundary) {
    return null
  }

  const verb = isOpen ? 'closes' : 'opens'
  const headline = isOpen ? 'Market open' : 'Market closed'
  const boundaryEt = formatMarketClock(boundary)
  const earlyNote = isOpen && isEarlyClose(boundary) ? ' (early close)' : ''
  const className = `market-status-banner${isOpen ? ' is-open' : ' is-closed'}${data.is_stale ? ' is-stale' : ''}`

  return (
    <div
      className={className}
      role="status"
      aria-live="off"
      aria-label={`${headline}, ${verb} in ${describeCountdown(remainingMs)}`}
      data-testid="market-status-banner"
      data-state={isOpen ? 'open' : 'closed'}
    >
      <span className="market-status-banner__mark" aria-hidden="true" />
      <span className="market-status-banner__text">
        {headline} · {verb} in{' '}
        <span className="market-status-banner__countdown numeral" data-testid="market-status-countdown">
          {formatCountdown(remainingMs)}
        </span>
      </span>
      <span className="market-status-banner__hint" data-testid="market-status-hint">
        NYSE · {verb} {boundaryEt} ET{earlyNote} · {formatViewerClock(boundary)}
      </span>
    </div>
  )
}
