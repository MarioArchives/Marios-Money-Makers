import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi } from 'vitest'
import { StockRow } from './StockRow'
import type { StockRowProps } from './StockRow.props'
import type { StockSummary } from '../../../api/types'

function makeStock(overrides: Partial<StockSummary> = {}): StockSummary {
  return {
    ticker: 'AZN.L',
    name: 'AstraZeneca',
    sector: 'Pharma',
    price: 1234.56,
    currency: 'GBP',
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
    renderRow(makeStock({ name: 'AstraZeneca', ticker: 'AZN.L' }))

    expect(screen.getByText('AstraZeneca')).toBeInTheDocument()
    const icon = screen.getByRole('img', { name: 'AstraZeneca' }) as HTMLImageElement
    expect(icon.getAttribute('src')).toBe('/logos/AZN.L.svg')
  })

  it('renders the price formatted as pounds with thousands separator', () => {
    renderRow(makeStock({ price: 1234.56 }))

    expect(screen.getByText('£1,234.56')).toBeInTheDocument()
  })

  it('renders a small price without a thousands separator correctly', () => {
    renderRow(makeStock({ price: 112.34 }))

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
    renderRow(makeStock({ ticker: 'VOD.L' }))

    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/stock/VOD.L')
  })

  it('renders no staleness marker for a fresh row', () => {
    renderRow(makeStock({ is_stale: false, error: null }))

    expect(screen.getByTestId('stock-row').classList.contains('is-stale')).toBe(false)
    expect(screen.queryByTestId('stale-dot')).not.toBeInTheDocument()
  })
})

/**
 * v3 error UX (replaces the pre-v3 "renders an ErrorBadge reflecting
 * is_stale/error state" assertion): a stock without fresh data is greyed
 * out and keeps its last known figures, rather than being annotated with
 * the backend's error text.
 */
describe('StockRow degraded state', () => {
  it('greys the row out when the stock is stale', () => {
    renderRow(makeStock({ is_stale: true, error: null }))

    expect(screen.getByTestId('stock-row').classList.contains('is-stale')).toBe(true)
  })

  it('greys the row out when the stock carries an error, even if not flagged stale', () => {
    renderRow(makeStock({ is_stale: false, error: 'fetch failed for AZN.L' }))

    expect(screen.getByTestId('stock-row').classList.contains('is-stale')).toBe(true)
  })

  it('marks a degraded row with the subtle stale dot', () => {
    renderRow(makeStock({ is_stale: true, error: 'fetch failed for AZN.L' }))

    expect(screen.getByTestId('stale-dot')).toBeInTheDocument()
  })

  it('keeps showing the last known price and never renders the error text', () => {
    renderRow(makeStock({ price: 1234.56, is_stale: true, error: 'fetch failed for AZN.L' }))

    expect(screen.getByText('£1,234.56')).toBeInTheDocument()
    expect(screen.queryByText(/fetch failed/i)).not.toBeInTheDocument()
    expect(screen.getByTestId('stock-row').textContent).not.toContain('fetch failed')
  })

  it('falls back to an em dash when there is no last known price at all', () => {
    renderRow(makeStock({ price: null, is_stale: true, error: 'fetch failed for AZN.L' }))

    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('still renders the company name and link on a degraded row', () => {
    renderRow(makeStock({ ticker: 'VOD.L', name: 'Vodafone', is_stale: true }))

    expect(screen.getByText('Vodafone')).toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveAttribute('href', '/stock/VOD.L')
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
