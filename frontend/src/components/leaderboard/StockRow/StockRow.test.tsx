import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi } from 'vitest'
import { StockRow } from './StockRow'
import type { StockRowProps } from './StockRow.props'
import type { StockSummary } from '../../../api/types'
import { FxRateContext } from '../../../providers/FxRateProvider/FxRateProvider'

function makeStock(overrides: Partial<StockSummary> = {}): StockSummary {
  return {
    ticker: 'AAPL',
    name: 'Apple',
    sector: 'Technology',
    price: 1234.56,
    currency: 'USD',
    previous_close: 1200,
    change: 34.56,
    change_percent: 2.88,
    is_stale: false,
    error: null,
    ...overrides,
  }
}

function renderRow(stock: StockSummary) {
  return render(
    <MemoryRouter>
      <StockRow stock={stock} />
    </MemoryRouter>,
  )
}

describe('StockRow', () => {
  it('renders the company name and icon', () => {
    renderRow(makeStock({ name: 'Apple', ticker: 'AAPL' }))

    expect(screen.getByText('Apple')).toBeInTheDocument()
    const icon = screen.getByRole('img', { name: 'Apple' }) as HTMLImageElement
    expect(icon.getAttribute('src')).toBe('/logos/AAPL.svg')
  })

  it('renders the price in the API currency with thousands separator (no FX rate yet)', () => {
    renderRow(makeStock({ price: 1234.56, currency: 'USD' }))

    expect(screen.getByText('$1,234.56')).toBeInTheDocument()
  })

  it('renders a small price without a thousands separator correctly', () => {
    renderRow(makeStock({ price: 112.34 }))

    expect(screen.getByText('$112.34')).toBeInTheDocument()
  })

  it('follows the per-stock currency field rather than assuming one currency', () => {
    renderRow(makeStock({ price: 112.34, currency: 'GBP' }))

    expect(screen.getByText('£112.34')).toBeInTheDocument()
  })

  it('shows up styling when change_percent is positive', () => {
    renderRow(makeStock({ change_percent: 2.53 }))

    const indicator = screen.getByTestId('change-indicator')
    expect(indicator.classList.contains('up')).toBe(true)
  })

  it('shows down styling when change_percent is negative', () => {
    renderRow(makeStock({ change_percent: -1.2 }))

    const indicator = screen.getByTestId('change-indicator')
    expect(indicator.classList.contains('down')).toBe(true)
  })

  it('links to the stock detail page for the row ticker', () => {
    renderRow(makeStock({ ticker: 'MSFT' }))

    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/stock/MSFT')
  })

  it('renders no staleness marker for a fresh row', () => {
    renderRow(makeStock({ is_stale: false, error: null }))

    expect(screen.getByTestId('stock-row').classList.contains('is-stale')).toBe(false)
    expect(screen.queryByTestId('stale-dot')).not.toBeInTheDocument()
  })
})

/** Degraded state: greyed out with last known figures, no error text (v3 UX). */
describe('StockRow degraded state', () => {
  it('greys the row out when the stock is stale', () => {
    renderRow(makeStock({ is_stale: true, error: null }))

    expect(screen.getByTestId('stock-row').classList.contains('is-stale')).toBe(true)
  })

  it('greys the row out when the stock carries an error, even if not flagged stale', () => {
    renderRow(makeStock({ is_stale: false, error: 'fetch failed for AAPL' }))

    expect(screen.getByTestId('stock-row').classList.contains('is-stale')).toBe(true)
  })

  it('marks a degraded row with the subtle stale dot', () => {
    renderRow(makeStock({ is_stale: true, error: 'fetch failed for AAPL' }))

    expect(screen.getByTestId('stale-dot')).toBeInTheDocument()
  })

  it('keeps showing the last known price and never renders the error text', () => {
    renderRow(makeStock({ price: 1234.56, is_stale: true, error: 'fetch failed for AAPL' }))

    expect(screen.getByText('$1,234.56')).toBeInTheDocument()
    expect(screen.queryByText(/fetch failed/i)).not.toBeInTheDocument()
    expect(screen.getByTestId('stock-row').textContent).not.toContain('fetch failed')
  })

  it('falls back to an em dash when there is no last known price at all', () => {
    renderRow(makeStock({ price: null, is_stale: true, error: 'fetch failed for AAPL' }))

    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('still renders the company name and link on a degraded row', () => {
    renderRow(makeStock({ ticker: 'MSFT', name: 'Microsoft', is_stale: true }))

    expect(screen.getByText('Microsoft')).toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveAttribute('href', '/stock/MSFT')
  })
})

describe('StockRow memoization', () => {
  function getInnerRenderFn() {
    return (StockRow as unknown as { type: (props: StockRowProps) => JSX.Element }).type
  }

  function setInnerRenderFn(fn: (props: StockRowProps) => JSX.Element) {
    ;(StockRow as unknown as { type: (props: StockRowProps) => JSX.Element }).type = fn
  }

  it('does not re-execute its render body when re-rendered with the same stock object reference', () => {
    const original = getInnerRenderFn()
    const renderSpy = vi.fn(original)
    setInnerRenderFn(renderSpy)

    try {
      const stock = makeStock()

      function Harness() {
        const [, forceUpdate] = useState(0)
        return (
          <MemoryRouter>
            <button onClick={() => forceUpdate((n) => n + 1)}>rerender</button>
            <StockRow stock={stock} />
          </MemoryRouter>
        )
      }

      render(<Harness />)
      expect(renderSpy).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getByRole('button', { name: 'rerender' }))

      // Parent re-rendered (button click updated its own state) but `stock`
      // is the exact same object reference, so React.memo should bail out
      // and the wrapped render function must not run again.
      expect(renderSpy).toHaveBeenCalledTimes(1)
    } finally {
      setInnerRenderFn(original)
    }
  })

  it('does re-execute its render body when given a new stock object reference', () => {
    const original = getInnerRenderFn()
    const renderSpy = vi.fn(original)
    setInnerRenderFn(renderSpy)

    try {
      function Harness() {
        const [stock, setStock] = useState(makeStock())
        return (
          <MemoryRouter>
            <button onClick={() => setStock({ ...stock, price: (stock.price ?? 0) + 1 })}>change</button>
            <StockRow stock={stock} />
          </MemoryRouter>
        )
      }

      render(<Harness />)
      expect(renderSpy).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getByRole('button', { name: 'change' }))

      expect(renderSpy).toHaveBeenCalledTimes(2)
    } finally {
      setInnerRenderFn(original)
    }
  })
})

/** GBP conversion via FxRateProvider; native figure is the fallback with no rate. */
describe('StockRow GBP display', () => {
  const rate = { base: 'USD', quote: 'GBP', rate: 0.5, date: '2026-08-20', source: 'ECB' } as const

  function renderWithRate(stock: StockSummary) {
    return render(
      <FxRateContext.Provider value={{ status: 'ready', rate }}>
        <MemoryRouter>
          <StockRow stock={stock} />
        </MemoryRouter>
      </FxRateContext.Provider>,
    )
  }

  it('shows the USD price converted to GBP and never the dollar figure', () => {
    renderWithRate(makeStock({ price: 1234.56, currency: 'USD' }))

    expect(screen.getByText('£617.28')).toBeInTheDocument()
    expect(screen.queryByText('$1,234.56')).not.toBeInTheDocument()
  })

  it('leaves a GBP-denominated price untouched', () => {
    renderWithRate(makeStock({ price: 112.34, currency: 'GBP' }))

    expect(screen.getByText('£112.34')).toBeInTheDocument()
  })

  it('still shows an em dash when there has never been a price', () => {
    renderWithRate(makeStock({ price: null }))

    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
