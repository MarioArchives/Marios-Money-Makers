import { useEffect, useState } from 'react'
import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriceTicker } from './PriceTicker'
import type { PriceTickerProps } from './PriceTicker.props'
import type { HistoryResponse, StockSummary } from '../../../api/types'
import { FxRateContext } from '../../../providers/FxRateProvider/FxRateProvider'

/**
 * `queries.ts` is mocked below. Real `useQuery` re-renders only the
 * component that called it when ITS OWN cache entry changes (an internal
 * subscription), independently of any parent re-render. To test that
 * PriceTicker only reacts to `useStockDetailQuery` (never
 * `useStockHistoryQuery`), the mocked hooks are backed by tiny reactive
 * stores that subscribe/unsubscribe via `useEffect`, so changing one
 * store's value only re-renders components that actually called the
 * corresponding hook — mirroring real react-query subscription semantics
 * far more faithfully than a static `mockReturnValue` + forced rerender
 * would (which can't distinguish "which hook" a component depends on).
 */
interface ReactiveStore<T> {
  set(next: T): void
  subscribe(listener: () => void): () => void
  getSnapshot(): T
}

const { detailStore, historyStore } = vi.hoisted(() => {
  function createStore<T>(initial: T): ReactiveStore<T> {
    let value = initial
    const listeners = new Set<() => void>()
    return {
      set(next: T) {
        value = next
        listeners.forEach((listener) => listener())
      },
      subscribe(listener: () => void) {
        listeners.add(listener)
        return () => {
          listeners.delete(listener)
        }
      },
      getSnapshot() {
        return value
      },
    }
  }

  return {
    detailStore: createStore<unknown>(null),
    historyStore: createStore<unknown>(null),
  }
})

vi.mock('../../../api/queries', () => ({
  useStockDetailQuery: (_ticker: string) => {
    const [, forceUpdate] = useState(0)
    useEffect(
      () => (detailStore as ReactiveStore<unknown>).subscribe(() => forceUpdate((n) => n + 1)),
      [],
    )
    return {
      data: (detailStore as ReactiveStore<unknown>).getSnapshot(),
      isSuccess: true,
      isLoading: false,
      isError: false,
      error: null,
    }
  },
  useStockHistoryQuery: (_ticker: string) => {
    const [, forceUpdate] = useState(0)
    useEffect(
      () => (historyStore as ReactiveStore<unknown>).subscribe(() => forceUpdate((n) => n + 1)),
      [],
    )
    return {
      data: (historyStore as ReactiveStore<unknown>).getSnapshot(),
      isSuccess: true,
      isLoading: false,
      isError: false,
      error: null,
    }
  },
}))

const summary: StockSummary = {
  ticker: 'AZN.L',
  name: 'AstraZeneca',
  sector: 'Pharma',
  price: 112.34,
  currency: 'USD',
  previous_close: 110.0,
  change: 2.34,
  change_percent: 2.13,
  is_stale: false,
  error: null,
}

const history: HistoryResponse = {
  ticker: 'AZN.L',
  interval: '5m',
  range: '1d',
  points: [{ t: '2026-08-19T12:00:00Z', close: 112.0 }],
  is_stale: false,
  error: null,
}

beforeEach(() => {
  ;(detailStore as ReactiveStore<StockSummary | null>).set(summary)
  ;(historyStore as ReactiveStore<HistoryResponse | null>).set(history)
})

describe('PriceTicker rendering', () => {
  it('renders the current price sourced from the detail query', () => {
    render(<PriceTicker ticker="AZN.L" />)

    expect(screen.getByTestId('price-ticker').textContent).toContain('112.34')
  })

  it('formats the price in the currency the API reports', () => {
    render(<PriceTicker ticker="AZN.L" />)
    expect(screen.getByTestId('price-ticker').textContent).toContain('$112.34')

    ;(detailStore as ReactiveStore<StockSummary>).set({ ...summary, currency: 'GBP' })
    render(<PriceTicker ticker="AZN.L" />)
    expect(screen.getAllByTestId('price-ticker').at(-1)?.textContent).toContain('£112.34')
  })
})

describe('PriceTicker re-render isolation', () => {
  function getInnerRenderFn() {
    return (PriceTicker as unknown as { type: (props: PriceTickerProps) => JSX.Element }).type
  }

  function setInnerRenderFn(fn: (props: PriceTickerProps) => JSX.Element) {
    ;(PriceTicker as unknown as { type: (props: PriceTickerProps) => JSX.Element }).type = fn
  }

  it('does not re-render when only the history query value changes', () => {
    const original = getInnerRenderFn()
    const renderSpy = vi.fn(original)
    setInnerRenderFn(renderSpy)

    try {
      render(<PriceTicker ticker="AZN.L" />)
      expect(renderSpy).toHaveBeenCalledTimes(1)

      act(() => {
        ;(historyStore as ReactiveStore<HistoryResponse>).set({
          ...history,
          points: [...history.points, { t: '2026-08-19T12:05:00Z', close: 113.0 }],
        })
      })

      // PriceTicker never calls useStockHistoryQuery, so it must not have
      // resubscribed/re-rendered when only the history store changed.
      expect(renderSpy).toHaveBeenCalledTimes(1)
    } finally {
      setInnerRenderFn(original)
    }
  })

  it('re-renders when the detail query value changes', () => {
    const original = getInnerRenderFn()
    const renderSpy = vi.fn(original)
    setInnerRenderFn(renderSpy)

    try {
      render(<PriceTicker ticker="AZN.L" />)
      expect(renderSpy).toHaveBeenCalledTimes(1)

      act(() => {
        ;(detailStore as ReactiveStore<StockSummary>).set({
          ...summary,
          price: 115.0,
          change: 5.0,
          change_percent: 4.55,
        })
      })

      expect(renderSpy).toHaveBeenCalledTimes(2)
    } finally {
      setInnerRenderFn(original)
    }
  })
})

/**
 * v3 error UX: a stale/errored price greys out and keeps its last known
 * value; the backend's error string is never rendered.
 */
describe('PriceTicker degraded state', () => {
  it('is not marked stale while the figure is fresh', () => {
    render(<PriceTicker ticker="AZN.L" />)

    expect(screen.getByTestId('price-ticker').classList.contains('is-stale')).toBe(false)
    expect(screen.queryByTestId('stale-dot')).not.toBeInTheDocument()
  })

  it('greys out and keeps the last known price when the figure is stale', () => {
    ;(detailStore as ReactiveStore<StockSummary>).set({ ...summary, is_stale: true })

    render(<PriceTicker ticker="AZN.L" />)

    const ticker = screen.getByTestId('price-ticker')
    expect(ticker.classList.contains('is-stale')).toBe(true)
    expect(ticker.textContent).toContain('112.34')
    expect(screen.getByTestId('stale-dot')).toBeInTheDocument()
  })

  it('greys out without rendering the backend error text', () => {
    ;(detailStore as ReactiveStore<StockSummary>).set({
      ...summary,
      is_stale: true,
      error: 'fetch failed for AZN.L',
    })

    render(<PriceTicker ticker="AZN.L" />)

    const ticker = screen.getByTestId('price-ticker')
    expect(ticker.classList.contains('is-stale')).toBe(true)
    expect(ticker.textContent).not.toContain('fetch failed')
  })

  it('falls back to an em dash when there has never been a price', () => {
    ;(detailStore as ReactiveStore<StockSummary>).set({
      ...summary,
      price: null,
      change_percent: null,
      is_stale: true,
    })

    render(<PriceTicker ticker="AZN.L" />)

    expect(screen.getByTestId('price-ticker').textContent).toContain('—')
  })
})

describe('PriceTicker GBP display', () => {
  it('shows the price converted to GBP when the shared FX rate is available', () => {
    const rate = { base: 'USD', quote: 'GBP', rate: 0.5, date: '2026-08-20', source: 'ECB' } as const
    render(
      <FxRateContext.Provider value={{ status: 'ready', rate }}>
        <PriceTicker ticker="AZN.L" />
      </FxRateContext.Provider>,
    )

    expect(screen.getByTestId('price-ticker').textContent).toContain('£56.17')
    expect(screen.getByTestId('price-ticker').textContent).not.toContain('$')
  })
})
