import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { StockDetailPage } from './StockDetailPage'
import { useStockDetailQuery } from '../../api/queries'
import { CONNECTION_LOST_MESSAGE } from '../../components/shared/ConnectionBanner/ConnectionBanner'
import type { StockSummary } from '../../api/types'

vi.mock('../../api/queries', () => ({
  useStockDetailQuery: vi.fn(),
}))

// Isolate the page from the leaf components' own implementation status —
// the page's job is only to read the ticker and pass it (plus the header's
// name/sector) down, per the "minimal page, no live data state" convention.
vi.mock('../../components/stock-detail/StockHeader/StockHeader', () => ({
  StockHeader: (props: { ticker: string; name: string; sector: string }) => (
    <div data-testid="stock-header">{`${props.ticker}|${props.name}|${props.sector}`}</div>
  ),
}))

vi.mock('../../components/stock-detail/PriceTicker/PriceTicker', () => ({
  PriceTicker: (props: { ticker: string }) => (
    <div data-testid="price-ticker">{props.ticker}</div>
  ),
}))

vi.mock('../../components/stock-detail/StockChart/StockChart', () => ({
  StockChart: (props: { ticker: string }) => <div data-testid="stock-chart">{props.ticker}</div>,
}))

vi.mock('../../components/stock-detail/RawDataTable/RawDataTable', () => ({
  RawDataTable: (props: { ticker: string }) => (
    <div data-testid="raw-data-table">{props.ticker}</div>
  ),
}))

const mockedUseStockDetailQuery = vi.mocked(useStockDetailQuery)

function makeSummary(overrides: Partial<StockSummary> = {}): StockSummary {
  return {
    ticker: 'AZN.L',
    name: 'AstraZeneca',
    sector: 'Pharma',
    price: 112.34,
    currency: 'GBP',
    previous_close: 110,
    change: 2.34,
    change_percent: 2.13,
    is_stale: false,
    error: null,
    ...overrides,
  }
}

function mockDetailQuery(data: StockSummary | undefined): void {
  mockedUseStockDetailQuery.mockReturnValue({
    data,
    error: null,
    isLoading: data === undefined,
    isSuccess: data !== undefined,
    isError: false,
  } as unknown as UseQueryResult<StockSummary, Error>)
}

function mockDetailQueryError(data: StockSummary | undefined): void {
  mockedUseStockDetailQuery.mockReturnValue({
    data,
    error: new Error('Failed to fetch'),
    isLoading: false,
    isSuccess: false,
    isError: true,
  } as unknown as UseQueryResult<StockSummary, Error>)
}

function renderAtRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/stock/:ticker" element={<StockDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('StockDetailPage', () => {
  beforeEach(() => {
    mockedUseStockDetailQuery.mockReset()
    mockDetailQuery(makeSummary())
  })

  it('reads the ticker from the route params and passes it to PriceTicker', () => {
    renderAtRoute('/stock/AZN.L')

    expect(screen.getByTestId('price-ticker')).toHaveTextContent('AZN.L')
  })

  it('passes the same ticker down to StockChart', () => {
    renderAtRoute('/stock/AZN.L')

    expect(screen.getByTestId('stock-chart')).toHaveTextContent('AZN.L')
  })

  it('renders StockHeader with the ticker, name and sector', () => {
    renderAtRoute('/stock/AZN.L')

    expect(screen.getByTestId('stock-header')).toHaveTextContent('AZN.L|AstraZeneca|Pharma')
  })

  it('renders header and all leaf components together for a given route', () => {
    renderAtRoute('/stock/AZN.L')

    expect(screen.getByTestId('stock-header')).toBeInTheDocument()
    expect(screen.getByTestId('price-ticker')).toBeInTheDocument()
    expect(screen.getByTestId('stock-chart')).toBeInTheDocument()
    expect(screen.getByTestId('raw-data-table')).toBeInTheDocument()
  })

  it('renders the RawDataTable below the chart with the route ticker', () => {
    renderAtRoute('/stock/AZN.L')

    const chart = screen.getByTestId('stock-chart')
    const table = screen.getByTestId('raw-data-table')
    expect(table).toHaveTextContent('AZN.L')
    expect(chart.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('does not render the communication-loss message while the query is healthy', () => {
    renderAtRoute('/stock/AZN.L')

    expect(screen.queryByText(CONNECTION_LOST_MESSAGE)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('passes a different ticker down to all children for a different route', () => {
    mockDetailQuery(makeSummary({ ticker: 'GSK.L', name: 'GSK', sector: 'Pharma' }))

    renderAtRoute('/stock/GSK.L')

    expect(screen.getByTestId('stock-header')).toHaveTextContent('GSK.L|GSK|Pharma')
    expect(screen.getByTestId('price-ticker')).toHaveTextContent('GSK.L')
    expect(screen.getByTestId('stock-chart')).toHaveTextContent('GSK.L')
    expect(screen.getByTestId('raw-data-table')).toHaveTextContent('GSK.L')
  })
})

/**
 * v3 error UX: when the backend is unreachable the detail page keeps every
 * surface mounted with its last figures and greys them out behind ONE calm
 * page-level message — no spinner, no torn-down chart, no raw error text.
 */
describe('StockDetailPage when the backend is unreachable', () => {
  beforeEach(() => {
    mockedUseStockDetailQuery.mockReset()
  })

  it('renders a single communication-loss message when the detail query errors', () => {
    mockDetailQueryError(makeSummary())

    renderAtRoute('/stock/AZN.L')

    expect(screen.getAllByText(CONNECTION_LOST_MESSAGE)).toHaveLength(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/failed to fetch/i)).not.toBeInTheDocument()
  })

  it('keeps the header, price, chart and raw data table mounted with the last data', () => {
    mockDetailQueryError(makeSummary())

    renderAtRoute('/stock/AZN.L')

    expect(screen.getByTestId('stock-header')).toHaveTextContent('AZN.L|AstraZeneca|Pharma')
    expect(screen.getByTestId('price-ticker')).toHaveTextContent('AZN.L')
    expect(screen.getByTestId('stock-chart')).toHaveTextContent('AZN.L')
    expect(screen.getByTestId('raw-data-table')).toHaveTextContent('AZN.L')
  })

  it('greys the page out while disconnected', () => {
    mockDetailQueryError(makeSummary())

    const { container } = renderAtRoute('/stock/AZN.L')

    const page = container.querySelector('.stock-detail-page')
    expect(page?.classList.contains('is-disconnected')).toBe(true)
  })

  it('still shows the message when the query errors before any data arrived', () => {
    mockDetailQueryError(undefined)

    renderAtRoute('/stock/AZN.L')

    expect(screen.getByText(CONNECTION_LOST_MESSAGE)).toBeInTheDocument()
    expect(screen.getByTestId('stock-chart')).toBeInTheDocument()
  })
})
