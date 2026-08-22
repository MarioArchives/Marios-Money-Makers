import { useContext, useEffect, useState } from 'react'
import { QueryClientContext, type Query } from '@tanstack/react-query'
import { POLL_INTERVAL_MS, msUntilNextPoll } from '../api/queries'

export interface PollCountdown {
  /** Fraction of the poll interval still to run, 1 -> 0. 1 when nothing is polling yet. */
  remaining: number
  /** Whole seconds until the next fetch (0 while nothing is polling yet). */
  secondsLeft: number
  /** True while any polled stock query is in flight. */
  isFetching: boolean
}

/** How often the countdown re-evaluates; the bar's CSS transition smooths between ticks. */
export const COUNTDOWN_TICK_MS = 250

/** The polled data queries (`['stocks']`, `['stock', ...]`), all on the shared poll tick. */
function isPolledStockQuery(query: Query): boolean {
  const head = query.queryKey[0]
  return head === 'stocks' || head === 'stock'
}

/**
 * Countdown to the next data poll. Every polled stock query refetches on
 * the same wall-clock tick (`alignedPollInterval` in api/queries), so the
 * countdown is simply the time to that shared tick — one cycle, draining
 * 1 -> 0 and snapping back to 1 on the tick — regardless of how many
 * queries are active or when each of them happened to mount. A query that
 * mounts (or refetches) mid-cycle — e.g. switching the stock page's range —
 * neither resets nor dents the bar. No provider (unit tests) or no polled
 * data yet -> `remaining: 1`, i.e. a full, static bar.
 */
export function usePollCountdown(intervalMs: number = POLL_INTERVAL_MS): PollCountdown {
  const client = useContext(QueryClientContext)
  const [countdown, setCountdown] = useState<PollCountdown>({
    remaining: 1,
    secondsLeft: 0,
    isFetching: false,
  })

  useEffect(() => {
    if (!client) {
      return
    }
    const cache = client.getQueryCache()

    const evaluate = (): void => {
      const queries = cache.findAll({ type: 'active' }).filter(isPolledStockQuery)
      const isFetching = queries.some((query) => query.state.fetchStatus === 'fetching')
      const hasPolled = queries.some((query) => query.state.dataUpdatedAt > 0)
      if (!hasPolled) {
        setCountdown({ remaining: 1, secondsLeft: 0, isFetching })
        return
      }
      const msLeft = msUntilNextPoll(Date.now(), intervalMs)
      setCountdown({
        remaining: msLeft / intervalMs,
        secondsLeft: Math.ceil(msLeft / 1000),
        isFetching,
      })
    }

    evaluate()
    const unsubscribe = cache.subscribe(evaluate)
    const timer = window.setInterval(evaluate, COUNTDOWN_TICK_MS)
    return () => {
      unsubscribe()
      window.clearInterval(timer)
    }
  }, [client, intervalMs])

  return countdown
}
