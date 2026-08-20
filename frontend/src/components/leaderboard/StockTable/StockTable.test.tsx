import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { StockTable } from './StockTable'
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

const STOCKS: StockSummary[] = [
  makeStock({ ticker: 'AZN.L', name: 'AstraZeneca' }),
  makeStock({ ticker: 'GSK.L', name: 'GSK' }),
  makeStock({ ticker: 'VOD.L', name: 'Vodafone' }),
]

function renderTable(stocks: StockSummary[]) {
  return render(
    <MemoryRouter>
      <StockTable stocks={stocks} />
    </MemoryRouter>,
  )
}

describe('StockTable', () => {
  it('renders one row (link to the stock detail page) per stock', () => {
    renderTable(STOCKS)

    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(STOCKS.length)
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/stock/AZN.L',
      '/stock/GSK.L',
      '/stock/VOD.L',
    ])
  })

  it("renders each stock's name", () => {
    renderTable(STOCKS)

    for (const stock of STOCKS) {
      expect(screen.getByText(stock.name)).toBeInTheDocument()
    }
  })

  it('renders no rows for an empty stock list', () => {
    renderTable([])

    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })
})
