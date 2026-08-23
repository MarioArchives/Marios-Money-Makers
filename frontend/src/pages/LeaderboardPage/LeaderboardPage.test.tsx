import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { LeaderboardPage } from './LeaderboardPage'
import { useMarketClockQuery, useStocksQuery } from '../../api/queries'
import { CONNECTION_LOST_MESSAGE } from '../../components/shared/ConnectionBanner/ConnectionBanner'
import type { MarketClockResponse, StockSummary, StocksResponse } from '../../api/types'

vi.mock('../../api/queries', () => ({
  useStocksQuery: vi.fn(),
  useMarketClockQuery: vi.fn(),
}))

// Isolate the page from StockTable's own rendering/implementation status —
// LeaderboardPage's job is only to decide *whether* to render StockTable
// and *what* to pass it, per the "minimal page" convention.
vi.mock('../../components/leaderboard/StockTable/StockTable', () => ({
  StockTable: ({ stocks, isDegraded }: { stocks: StockSummary[]; isDegraded?: boolean }) => (
    <div data-testid="stock-table" data-degraded={String(Boolean(isDegraded))}>
      {stocks.map((s) => s.ticker).join(',')}
    </div>
  ),
}))

const mockedUseStocksQuery = vi.mocked(useStocksQuery)
const mockedUseMarketClockQuery = vi.mocked(useMarketClockQuery)

function makeStock(overrides: Partial<StockSummary> = {}): StockSummary {
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

function queryResult(
  overrides: Partial<UseQueryResult<StocksResponse, Error>>,
): UseQueryResult<StocksResponse, Error> {
  return {
    data: undefined,
    error: null,
    isLoading: false,
    isPending: false,
    isSuccess: false,
    isError: false,
    ...overrides,
  } as unknown as UseQueryResult<StocksResponse, Error>
}

function renderPage() {
  return render(
    <MemoryRouter>
      <LeaderboardPage />
    </MemoryRouter>,
  )
}

describe('LeaderboardPage', () => {
  beforeEach(() => {
    mockedUseStocksQuery.mockReset()
    // The MarketStatusBanner reads the clock query; while it is loading the
    // banner renders nothing, which keeps these page tests about the board.
    mockedUseMarketClockQuery.mockReset()
    mockedUseMarketClockQuery.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isPending: true,
      isSuccess: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as UseQueryResult<MarketClockResponse, Error>)
  })

  it('renders a loading state while the query is pending', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({ isLoading: true, isPending: true }),
    )

    renderPage()

    expect(screen.getByText(/loading/i)).toBeInTheDocument()
    expect(screen.queryByTestId('stock-table')).not.toBeInTheDocument()
  })

  /** v3 error UX: the backend being unreachable produces ONE calm message, and the last successfully fetched board stays on screen behind it. */
  it('shows a single calm communication-loss message when the query errors', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({ isError: true, error: new Error('network down') }),
    )

    renderPage()

    expect(screen.getAllByText(CONNECTION_LOST_MESSAGE)).toHaveLength(1)
    // Calm, not a red wall: announced politely, and the raw error is never shown.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/network down/i)).not.toBeInTheDocument()
  })

  it('keeps the last successfully fetched table, greyed out, when the query errors', () => {
    const stocks = [
      makeStock({ ticker: 'AZN.L', name: 'AstraZeneca' }),
      makeStock({ ticker: 'GSK.L', name: 'GSK' }),
    ]
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isError: true,
        error: new Error('network down'),
        data: { updated_at: '2026-08-19T12:00:00Z', stocks },
      }),
    )

    renderPage()

    expect(screen.getByTestId('stock-table')).toHaveTextContent('AZN.L,GSK.L')
    expect(screen.getByTestId('stock-table')).toHaveAttribute('data-degraded', 'true')
    expect(screen.getByText(CONNECTION_LOST_MESSAGE)).toBeInTheDocument()
  })

  it('shows an empty state rather than a spinner when the query errors with no data ever', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({ isError: true, error: new Error('network down') }),
    )

    renderPage()

    expect(screen.getByText(CONNECTION_LOST_MESSAGE)).toBeInTheDocument()
    expect(screen.getByText(/no figures received yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('stock-table')).not.toBeInTheDocument()
  })

  it('does not grey the table out while the query is healthy', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: { updated_at: '2026-08-19T12:00:00Z', stocks: [makeStock()] },
      }),
    )

    renderPage()

    expect(screen.getByTestId('stock-table')).toHaveAttribute('data-degraded', 'false')
  })

  it('renders StockTable with the fetched stocks on success', () => {
    const stocks = [
      makeStock({ ticker: 'AZN.L', name: 'AstraZeneca' }),
      makeStock({ ticker: 'GSK.L', name: 'GSK' }),
    ]
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: { updated_at: '2026-08-19T12:00:00Z', stocks },
      }),
    )

    renderPage()

    expect(screen.getByTestId('stock-table')).toHaveTextContent('AZN.L,GSK.L')
  })

  it('does not render the communication-loss message on success', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: { updated_at: '2026-08-19T12:00:00Z', stocks: [] },
      }),
    )

    renderPage()

    expect(screen.queryByText(CONNECTION_LOST_MESSAGE)).not.toBeInTheDocument()
    expect(screen.queryByTestId('connection-banner')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not render a loading state once data has resolved', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: { updated_at: '2026-08-19T12:00:00Z', stocks: [makeStock()] },
      }),
    )

    renderPage()

    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument()
  })
})

describe('LeaderboardPage market status banner', () => {
  // These tests are about the page composition, so the clock query is
  // resolved (the banner renders nothing until it is).
  beforeEach(() => {
    mockedUseMarketClockQuery.mockReturnValue({
      data: {
        timestamp: '2026-08-19T12:00:00Z',
        is_open: false,
        next_open: '2199-01-02T14:30:00Z',
        next_close: '2199-01-02T21:00:00Z',
        fetched_at: '2026-08-19T12:00:00Z',
        is_stale: false,
        error: null,
      },
      error: null,
      isLoading: false,
      isPending: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as unknown as UseQueryResult<MarketClockResponse, Error>)
  })

  it('shows the market-open countdown as the first thing on the loaded page', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: { updated_at: '2026-08-19T12:00:00Z', stocks: [makeStock()] },
      }),
    )

    renderPage()

    const banner = screen.getByTestId('market-status-banner')
    expect(banner).toHaveTextContent(/^Market (open · closes|closed · opens) in /)
    expect(banner.parentElement).toHaveClass('leaderboard-page')
    expect(banner.parentElement?.firstElementChild).toBe(banner)
    expect(screen.getByTestId('stock-table')).toBeInTheDocument()
  })

  it('still shows the market status in the empty/error state', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({ isError: true, error: new Error('network down') }),
    )

    renderPage()

    expect(screen.getByTestId('market-status-banner')).toBeInTheDocument()
    expect(screen.getByText(CONNECTION_LOST_MESSAGE)).toBeInTheDocument()
  })
})

describe('LeaderboardPage ranking', () => {
  it('passes the board to StockTable ranked by change_percent, highest first, nulls last', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: {
          updated_at: '2026-08-19T12:00:00Z',
          stocks: [
            makeStock({ ticker: 'LOW', change_percent: -2 }),
            makeStock({ ticker: 'NONE', change_percent: null }),
            makeStock({ ticker: 'HIGH', change_percent: 3 }),
            makeStock({ ticker: 'MID', change_percent: 0.5 }),
          ],
        },
      }),
    )

    renderPage()

    expect(screen.getByTestId('stock-table')).toHaveTextContent('HIGH,MID,LOW,NONE')
  })

  it('re-ranks when a poll changes the figures', () => {
    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: {
          updated_at: '2026-08-19T12:00:00Z',
          stocks: [makeStock({ ticker: 'A', change_percent: 1 }), makeStock({ ticker: 'B', change_percent: 2 })],
        },
      }),
    )
    const { rerender } = renderPage()
    expect(screen.getByTestId('stock-table')).toHaveTextContent('B,A')

    mockedUseStocksQuery.mockReturnValue(
      queryResult({
        isSuccess: true,
        data: {
          updated_at: '2026-08-19T12:00:20Z',
          stocks: [makeStock({ ticker: 'A', change_percent: 5 }), makeStock({ ticker: 'B', change_percent: 2 })],
        },
      }),
    )
    rerender(
      <MemoryRouter>
        <LeaderboardPage />
      </MemoryRouter>,
    )
    expect(screen.getByTestId('stock-table')).toHaveTextContent('A,B')
  })
})
