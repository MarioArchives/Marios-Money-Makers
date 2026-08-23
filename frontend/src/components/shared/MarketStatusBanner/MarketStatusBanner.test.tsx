import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { MarketStatusBanner } from './MarketStatusBanner'
import { useMarketClockQuery } from '../../../api/queries'
import type { MarketClockResponse } from '../../../api/types'

// Query module mocked so these tests say "given this clock, render this".
vi.mock('../../../api/queries', () => ({
  useMarketClockQuery: vi.fn(),
}))

const mockedUseMarketClockQuery = vi.mocked(useMarketClockQuery)

// Wednesday 19 Aug 2026. New York is on EDT (UTC-4): the session runs
// 13:30Z-20:00Z.
const BEFORE_OPEN = new Date('2026-08-19T13:12:57Z') // 09:12:57 ET -> opens in 17m 03s
const IN_SESSION = new Date('2026-08-19T17:54:50Z') // 13:54:50 ET -> closes in 2h 05m 10s
const FRIDAY_EVENING = new Date('2026-08-21T21:00:00Z') // Fri 17:00 ET -> Mon 09:30 ET
const EARLY_CLOSE_DAY = new Date('2026-11-27T16:00:00Z') // Fri 11:00 EST, closes 13:00 EST

function clock(overrides: Partial<MarketClockResponse> = {}): MarketClockResponse {
  return {
    timestamp: '2026-08-19T13:12:57Z',
    is_open: false,
    next_open: '2026-08-19T13:30:00Z',
    next_close: '2026-08-19T20:00:00Z',
    fetched_at: '2026-08-19T13:12:57Z',
    is_stale: false,
    error: null,
    ...overrides,
  }
}

const CLOSED = clock()
const OPEN = clock({
  timestamp: '2026-08-19T17:54:50Z',
  is_open: true,
  next_open: '2026-08-20T13:30:00Z',
  next_close: '2026-08-19T20:00:00Z',
})
const OPEN_EARLY_CLOSE = clock({
  timestamp: '2026-11-27T16:00:00Z',
  is_open: true,
  next_open: '2026-11-30T14:30:00Z',
  next_close: '2026-11-27T18:00:00Z', // 13:00 EST
})
const WEEKEND = clock({
  timestamp: '2026-08-21T21:00:00Z',
  is_open: false,
  next_open: '2026-08-24T13:30:00Z',
  next_close: '2026-08-24T20:00:00Z',
})

function queryResult(
  overrides: Partial<UseQueryResult<MarketClockResponse, Error>>,
): UseQueryResult<MarketClockResponse, Error> {
  return {
    data: undefined,
    error: null,
    isLoading: false,
    isPending: false,
    isSuccess: false,
    isError: false,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as UseQueryResult<MarketClockResponse, Error>
}

/** Mock the hook to a resolved clock; returns the `refetch` spy. */
function mockClock(data: MarketClockResponse): ReturnType<typeof vi.fn> {
  const refetch = vi.fn()
  mockedUseMarketClockQuery.mockReturnValue(queryResult({ data, isSuccess: true, refetch }))
  return refetch
}

const banner = (): HTMLElement => screen.getByTestId('market-status-banner')
const countdown = (): HTMLElement => screen.getByTestId('market-status-countdown')

beforeEach(() => {
  vi.useFakeTimers()
  mockedUseMarketClockQuery.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('MarketStatusBanner', () => {
  it('says the market is closed and counts down to the backend\'s next_open', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockClock(CLOSED)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveAttribute('data-state', 'closed')
    expect(banner()).toHaveTextContent('Market closed · opens in 17m 03s')
    expect(screen.getByTestId('market-status-hint')).toHaveTextContent('NYSE · opens 09:30 ET')
  })

  it('ticks the countdown down every second from the local clock without refetching', () => {
    vi.setSystemTime(BEFORE_OPEN)
    const refetch = mockClock(CLOSED)
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
    expect(refetch).not.toHaveBeenCalled()
  })

  it('says the market is open and counts down to next_close during the session', () => {
    vi.setSystemTime(IN_SESSION)
    mockClock(OPEN)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveAttribute('data-state', 'open')
    expect(banner()).toHaveTextContent('Market open · closes in 2h 05m 10s')
    expect(screen.getByTestId('market-status-hint')).toHaveTextContent('NYSE · closes 16:00 ET')
    expect(screen.getByTestId('market-status-hint')).not.toHaveTextContent('early close')
  })

  it('flags an early close when next_close lands before 16:00 ET', () => {
    vi.setSystemTime(EARLY_CLOSE_DAY)
    mockClock(OPEN_EARLY_CLOSE)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveAttribute('data-state', 'open')
    expect(banner()).toHaveTextContent('Market open · closes in 2h 00m 00s')
    expect(screen.getByTestId('market-status-hint')).toHaveTextContent(
      'NYSE · closes 13:00 ET (early close)',
    )
  })

  it('uses the day form over a weekend', () => {
    vi.setSystemTime(FRIDAY_EVENING)
    mockClock(WEEKEND)
    render(<MarketStatusBanner />)

    expect(banner()).toHaveTextContent('Market closed · opens in 2d 16h 30m')
  })

  it('clamps at zero and refetches once when the boundary passes, then flips on the new clock', () => {
    vi.setSystemTime(new Date('2026-08-19T13:29:59Z'))
    const refetch = mockClock(CLOSED)
    render(<MarketStatusBanner />)
    expect(banner()).toHaveAttribute('data-state', 'closed')
    expect(countdown()).toHaveTextContent('0m 01s')
    expect(refetch).not.toHaveBeenCalled()

    // 13:30:00Z: the open we were counting down to has arrived. The
    // stale clock still says closed, so the countdown clamps at zero and
    // exactly one refetch is asked for.
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(countdown()).toHaveTextContent('0m 00s')
    expect(banner()).toHaveAttribute('data-state', 'closed')
    expect(refetch).toHaveBeenCalledTimes(1)

    // More ticks on the same stale clock: still clamped, still one refetch.
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(countdown()).toHaveTextContent('0m 00s')
    expect(refetch).toHaveBeenCalledTimes(1)

    // The refetched clock arrives (mocked hook now returns it): the banner
    // flips to open on its next tick and counts down to the close.
    mockedUseMarketClockQuery.mockReturnValue(
      queryResult({ data: OPEN, isSuccess: true, refetch }),
    )
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(banner()).toHaveAttribute('data-state', 'open')
    expect(banner()).toHaveTextContent('Market open · closes in 6h 29m 57s')
  })

  it('still renders a stale clock, marked as stale', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockClock(clock({ is_stale: true, error: 'alpaca error: connection refused' }))
    render(<MarketStatusBanner />)

    expect(banner()).toHaveClass('is-stale')
    expect(banner()).toHaveTextContent('Market closed · opens in 17m 03s')
  })

  it('is not marked stale for a fresh clock', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockClock(CLOSED)
    render(<MarketStatusBanner />)

    expect(banner()).not.toHaveClass('is-stale')
  })

  it('renders nothing while the clock is still loading', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockedUseMarketClockQuery.mockReturnValue(
      queryResult({ isLoading: true, isPending: true }),
    )
    render(<MarketStatusBanner />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByTestId('market-status-banner')).not.toBeInTheDocument()
  })

  it('renders nothing when the clock query errors with no data', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockedUseMarketClockQuery.mockReturnValue(
      queryResult({ isError: true, error: new Error('API error 503 for /api/market/clock') }),
    )
    render(<MarketStatusBanner />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('is a status region that does not announce every tick', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockClock(CLOSED)
    render(<MarketStatusBanner />)

    const region = screen.getByRole('status')
    expect(region).toHaveAttribute('aria-live', 'off')
    // The accessible name summarises the state to the minute — no seconds.
    expect(region).toHaveAttribute('aria-label', 'Market closed, opens in 17 minutes')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('clears its interval on unmount', () => {
    vi.setSystemTime(BEFORE_OPEN)
    mockClock(CLOSED)
    const { unmount } = render(<MarketStatusBanner />)
    expect(vi.getTimerCount()).toBe(1)

    unmount()

    expect(vi.getTimerCount()).toBe(0)
  })
})
