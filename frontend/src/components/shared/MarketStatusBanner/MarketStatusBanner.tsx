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
 * Countdown banner above the leaderboard ("Market closed · opens in 4h
 * 17m 03s"); renders nothing until the clock query has data. Refetches
 * once per boundary crossing; see ARCHITECTURE.md for the aria-live rationale.
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
