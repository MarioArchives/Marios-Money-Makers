import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketStatusBanner } from './MarketStatusBanner'

// Wednesday 19 Aug 2026. New York is on EDT (UTC-4): the session runs
// 13:30Z-20:00Z.
const BEFORE_OPEN = new Date('2026-08-19T13:12:57Z') // 09:12:57 ET -> opens in 17m 03s
const IN_SESSION = new Date('2026-08-19T17:54:50Z') // 13:54:50 ET -> closes in 2h 05m 10s
const FRIDAY_EVENING = new Date('2026-08-21T21:00:00Z') // Fri 17:00 ET -> Mon 09:30 ET

const banner = (): HTMLElement => screen.getByTestId('market-status-banner')
const countdown = (): HTMLElement => screen.getByTestId('market-status-countdown')

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('MarketStatusBanner', () => {
  it('says the market is closed and counts down to the next open', () => {
    vi.setSystemTime(BEFORE_OPEN)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveAttribute('data-state', 'closed')
    expect(banner()).toHaveTextContent('Market closed · opens in 17m 03s')
    expect(screen.getByTestId('market-status-hint')).toHaveTextContent('NYSE · opens 09:30 ET')
  })

  it('ticks the countdown down every second', () => {
    vi.setSystemTime(BEFORE_OPEN)
    render(<MarketStatusBanner />)
    expect(countdown()).toHaveTextContent('17m 03s')

    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(countdown()).toHaveTextContent('17m 02s')

    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(countdown()).toHaveTextContent('17m 00s')
  })

  it('says the market is open and counts down to the close during the session', () => {
    vi.setSystemTime(IN_SESSION)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveAttribute('data-state', 'open')
    expect(banner()).toHaveTextContent('Market open · closes in 2h 05m 10s')
    expect(screen.getByTestId('market-status-hint')).toHaveTextContent('NYSE · closes 16:00 ET')
  })

  it('flips from closed to open as the clock crosses 09:30 ET', () => {
    vi.setSystemTime(new Date('2026-08-19T13:29:59Z'))
    render(<MarketStatusBanner />)
    expect(banner()).toHaveAttribute('data-state', 'closed')

    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(banner()).toHaveAttribute('data-state', 'open')
    expect(banner()).toHaveTextContent('Market open · closes in 6h 30m 00s')
  })

  it('uses the day form over a weekend', () => {
    vi.setSystemTime(FRIDAY_EVENING)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveTextContent('Market closed · opens in 2d 16h 30m')
  })

  it('is a status region that does not announce every tick', () => {
    vi.setSystemTime(BEFORE_OPEN)
    render(<MarketStatusBanner />)

    const region = screen.getByRole('status')
    expect(region).toHaveAttribute('aria-live', 'off')
    // The accessible name summarises the state to the minute — no seconds.
    expect(region).toHaveAttribute('aria-label', 'Market closed, opens in 17 minutes')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('clears its interval on unmount', () => {
    vi.setSystemTime(BEFORE_OPEN)
    const { unmount } = render(<MarketStatusBanner />)
    expect(vi.getTimerCount()).toBe(1)

    unmount()

    expect(vi.getTimerCount()).toBe(0)
  })
})
