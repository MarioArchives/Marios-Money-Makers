import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { PollCountdownBar } from './PollCountdownBar'
import { POLL_INTERVAL_MS } from '../../../api/queries'

/** Mounts an observer on `['stocks']` so the query counts as active, then the bar. */
function Harness({ queryFn }: { queryFn: () => Promise<unknown> }): JSX.Element {
  useQuery({ queryKey: ['stocks'], queryFn, staleTime: Infinity })
  return <PollCountdownBar />
}

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

const valueNow = (): number => Number(screen.getByRole('progressbar').getAttribute('aria-valuenow'))

/** A wall-clock instant that is exactly on a poll tick, so a cycle starts at 100. */
const TICK_ZERO = POLL_INTERVAL_MS * 1_000_000

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(TICK_ZERO)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('PollCountdownBar', () => {
  it('is a full, static border when there is no query client at all', () => {
    render(<PollCountdownBar />)

    const bar = screen.getByRole('progressbar', { name: /next data refresh/i })
    expect(bar).toHaveAttribute('aria-valuenow', '100')
    expect(bar.classList.contains('is-fetching')).toBe(false)
  })

  it('drains from full to empty over the poll interval after data lands, then snaps back on the tick', async () => {
    const client = makeClient()
    client.setQueryData(['stocks'], { stocks: [] }, { updatedAt: Date.now() })

    render(
      <QueryClientProvider client={client}>
        <Harness queryFn={async () => ({ stocks: [] })} />
      </QueryClientProvider>,
    )

    expect(valueNow()).toBe(100)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS / 2)
    })
    expect(valueNow()).toBeGreaterThanOrEqual(45)
    expect(valueNow()).toBeLessThanOrEqual(55)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS / 2 - 250)
    })
    expect(valueNow()).toBeLessThanOrEqual(2)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 s until next refresh')

    // The shared tick: a new cycle starts at full.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250)
    })
    expect(valueNow()).toBe(100)
  })

  // Regression: on the stock page, switching 1d -> 30d -> all mounts a new
  // history query mid-cycle. Its fetch landing must neither refill the bar
  // nor jump it to some other partial value — every polled query refetches
  // on the same tick, and the bar shows only that one cycle.
  it('keeps the one shared cycle when another polled query lands mid-cycle (range switch)', async () => {
    const client = makeClient()
    client.setQueryData(['stocks'], { stocks: [] }, { updatedAt: Date.now() })
    render(
      <QueryClientProvider client={client}>
        <Harness queryFn={async () => ({ stocks: [] })} />
      </QueryClientProvider>,
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000)
    })
    const before = valueNow()
    expect(before).toBeGreaterThan(20)
    expect(before).toBeLessThan(30)

    act(() => {
      client.setQueryData(['stock', 'AZN.L', 'history', '30d'], { points: [] }, { updatedAt: Date.now() })
      client.setQueryData(['stocks'], { stocks: [1] }, { updatedAt: Date.now() })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(valueNow()).toBe(before)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })
    expect(valueNow()).toBe(100)
  })

  // Regression: switching *back* to a range that is already cached (and
  // stale) refetches it on mount. That in-flight, old-data query must not
  // drop the bar to 0 or otherwise disturb the cycle; it only pulses.
  it('does not dent the cycle when a stale cached query remounts and refetches mid-cycle', async () => {
    const client = makeClient()
    client.setQueryData(['stocks'], { stocks: [] }, { updatedAt: Date.now() })
    client.setQueryData(['stock', 'AZN.L', 'history', '1d'], { points: [] }, {
      updatedAt: Date.now() - 3 * POLL_INTERVAL_MS,
    })
    function StaleRange(): JSX.Element {
      // staleTime 0 -> refetches on mount; never resolves -> stays in flight.
      useQuery({
        queryKey: ['stock', 'AZN.L', 'history', '1d'],
        queryFn: () => new Promise(() => undefined),
        staleTime: 0,
      })
      return <Harness queryFn={async () => ({ stocks: [] })} />
    }
    function Page({ showRange }: { showRange: boolean }): JSX.Element {
      return (
        <QueryClientProvider client={client}>
          {showRange ? <StaleRange /> : <Harness queryFn={async () => ({ stocks: [] })} />}
        </QueryClientProvider>
      )
    }

    const { rerender } = render(<Page showRange={false} />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000)
    })
    const before = valueNow()
    expect(before).toBeGreaterThan(20)
    expect(before).toBeLessThan(30)

    rerender(<Page showRange />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250)
    })
    const bar = screen.getByRole('progressbar')
    expect(bar.classList.contains('is-fetching')).toBe(true)
    expect(valueNow()).toBeGreaterThan(20)
    expect(valueNow()).toBeLessThan(30)
  })

  it('pulses while a poll is in flight', async () => {
    const client = makeClient()
    // Never resolves: the query stays in `fetching`.
    render(
      <QueryClientProvider client={client}>
        <Harness queryFn={() => new Promise(() => undefined)} />
      </QueryClientProvider>,
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })

    const bar = screen.getByRole('progressbar')
    expect(bar.classList.contains('is-fetching')).toBe(true)
    expect(bar).toHaveAttribute('aria-valuetext', 'Refreshing')
  })

  it('ignores queries that are not the polled stock data', async () => {
    const client = makeClient()
    client.setQueryData(['fx', 'USD', 'GBP'], { rate: 1 }, { updatedAt: Date.now() - 15_000 })
    function FxOnly(): JSX.Element {
      useQuery({ queryKey: ['fx', 'USD', 'GBP'], queryFn: async () => ({ rate: 1 }), staleTime: Infinity })
      return <PollCountdownBar />
    }
    render(
      <QueryClientProvider client={client}>
        <FxOnly />
      </QueryClientProvider>,
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_300)
    })

    expect(valueNow()).toBe(100)
  })
})
