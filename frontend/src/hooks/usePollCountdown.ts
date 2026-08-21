import { useContext, useEffect, useState } from 'react'
import { QueryClientContext, type Query } from '@tanstack/react-query'
import { POLL_INTERVAL_MS } from '../api/queries'

export interface PollCountdown {
  /** Fraction of the poll interval still to run, 1 -> 0. 1 when nothing is polling yet. */
  remaining: number
  /** Whole seconds until the next fetch (0 while fetching / unknown). */
  secondsLeft: number
  /** True while any polled stock query is in flight. */
  isFetching: boolean
}

/** How often the countdown re-evaluates; the bar's CSS transition smooths between ticks. */
export const COUNTDOWN_TICK_MS = 250

/** The polled data queries (`['stocks']`, `['stock', ...]`), all on POLL_INTERVAL_MS. */
function isPolledStockQuery(query: Query): boolean {
  const head = query.queryKey[0]
  return head === 'stocks' || head === 'stock'
}

/**
 * Countdown to the next data poll, read straight from the React Query
 * cache: every active stock query refetches `POLL_INTERVAL_MS` after its
 * last `dataUpdatedAt`, so the next fetch is the earliest of those. No
 * provider (unit tests) or no polled query yet -> `remaining: 1`, i.e. a
 * full, static bar.
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
      let nextAt = Number.POSITIVE_INFINITY
      let isFetching = false
      for (const query of queries) {
        if (query.state.fetchStatus === 'fetching') {
          isFetching = true
        }
        if (query.state.dataUpdatedAt > 0) {
          nextAt = Math.min(nextAt, query.state.dataUpdatedAt + intervalMs)
        }
      }
      if (!Number.isFinite(nextAt)) {
        setCountdown({ remaining: 1, secondsLeft: 0, isFetching })
        return
      }
      const msLeft = Math.max(0, nextAt - Date.now())
      setCountdown({
        remaining: Math.min(1, msLeft / intervalMs),
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
