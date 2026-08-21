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

beforeEach(() => {
  vi.useFakeTimers()
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

  it('drains from full to empty over the poll interval after data lands', async () => {
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
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS / 2 + 500)
    })
    expect(valueNow()).toBe(0)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '0 s until next refresh')
  })

  it('refills to full when fresh data lands', async () => {
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
    expect(valueNow()).toBeLessThan(40)

    act(() => {
      client.setQueryData(['stocks'], { stocks: [1] }, { updatedAt: Date.now() })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(valueNow()).toBe(100)
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
      await vi.advanceTimersByTimeAsync(300)
    })

    expect(valueNow()).toBe(100)
  })
})
