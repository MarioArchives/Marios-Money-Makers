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

/** Countdown to the next data poll: the shared aligned tick (`alignedPollInterval`), draining 1 -> 0 and snapping back on tick regardless of which/how many queries are active. No provider or no polled data yet -> `remaining: 1`. */
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

    // Cache events can fire synchronously mid-render (React Query emits
    // `added` while building a new observer); defer to a microtask to avoid
    // a cross-component setState-during-render warning and coalesce bursts.
    let disposed = false
    let scheduled = false
    const onCacheEvent = (): void => {
      if (scheduled) {
        return
      }
      scheduled = true
      queueMicrotask(() => {
        scheduled = false
        if (!disposed) {
          evaluate()
        }
      })
    }

    evaluate()
    const unsubscribe = cache.subscribe(onCacheEvent)
    const timer = window.setInterval(evaluate, COUNTDOWN_TICK_MS)
    return () => {
      disposed = true
      unsubscribe()
      window.clearInterval(timer)
    }
  }, [client, intervalMs])

  return countdown
}
