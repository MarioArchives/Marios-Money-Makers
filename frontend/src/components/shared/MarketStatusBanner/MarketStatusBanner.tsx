import { useEffect, useState } from 'react'
import type { MarketStatusBannerProps } from './MarketStatusBanner.props'
import {
  describeCountdown,
  formatCountdown,
  formatMarketClock,
  formatViewerClock,
  getMarketStatus,
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
 * The status is pure (`getMarketStatus`), re-read from `Date.now()` once a
 * second; the interval is cleared on unmount.
 *
 * Accessibility: `role="status"` so it is findable as the page's status
 * line, but with `aria-live="off"` — a region that changes every second
 * would have a screen reader talking over everything else. The accessible
 * name (`aria-label`) carries the same state with the countdown rounded to
 * minutes, so a reader landing on it hears "Market closed, opens in 4 hours
 * 17 minutes" rather than the ticking seconds.
 */
export function MarketStatusBanner(_props: MarketStatusBannerProps): JSX.Element {
  void _props
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), MARKET_STATUS_TICK_MS)
    return () => window.clearInterval(timer)
  }, [])

  const status = getMarketStatus(now)
  const boundary = status.isOpen ? status.nextClose : status.nextOpen
  const remainingMs = boundary.getTime() - now.getTime()
  const verb = status.isOpen ? 'closes' : 'opens'
  const headline = status.isOpen ? 'Market open' : 'Market closed'
  const boundaryEt = formatMarketClock(boundary)
  const earlyNote = status.isOpen && status.isEarlyClose ? ' (early close)' : ''

  return (
    <div
      className={`market-status-banner${status.isOpen ? ' is-open' : ' is-closed'}`}
      role="status"
      aria-live="off"
      aria-label={`${headline}, ${verb} in ${describeCountdown(remainingMs)}`}
      data-testid="market-status-banner"
      data-state={status.isOpen ? 'open' : 'closed'}
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
