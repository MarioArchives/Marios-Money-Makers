import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { RawDataTable } from './RawDataTable'
import { useStockDetailQuery, useStockHistoryQuery } from '../../../api/queries'
import type { HistoryPoint, HistoryResponse, StockSummary } from '../../../api/types'

// Follows the same mocking pattern as StockChart.test.tsx: a static
// vi.mock factory with vi.fn() stand-ins, driven per-test via
// mockReturnValue. useStockDetailQuery is included in the mock (even
// though RawDataTable is not expected to call it) purely so the isolation
// test below can assert it was never invoked.
vi.mock('../../../api/queries', () => ({
  useStockHistoryQuery: vi.fn(),
  useStockDetailQuery: vi.fn(),
}))

const mockedUseStockHistoryQuery = vi.mocked(useStockHistoryQuery)
const mockedUseStockDetailQuery = vi.mocked(useStockDetailQuery)

function buildHistoryResult(points: HistoryPoint[]): UseQueryResult<HistoryResponse, Error> {
  return {
    data: {
      ticker: 'AZN.L',
      interval: '5m',
      range: '1d',
      points,
      is_stale: false,
      error: null,
    },
    isSuccess: true,
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as UseQueryResult<HistoryResponse, Error>
}

function buildDetailResult(): UseQueryResult<StockSummary, Error> {
  return {
    data: {
      ticker: 'AZN.L',
      name: 'AstraZeneca',
      sector: 'Pharma',
      price: 112.34,
      currency: 'GBP',
      previous_close: 110.0,
      change: 2.34,
      change_percent: 2.13,
      is_stale: false,
      error: null,
    },
    isSuccess: true,
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as UseQueryResult<StockSummary, Error>
}

beforeEach(() => {
  mockedUseStockHistoryQuery.mockReset()
  mockedUseStockDetailQuery.mockReset()
  mockedUseStockDetailQuery.mockReturnValue(buildDetailResult())
})

describe('RawDataTable rendering', () => {
  it('renders one table row per history point, showing the timestamp and close price', () => {
    const points: HistoryPoint[] = [
      { t: '2026-08-19T12:00:00Z', close: 100.12 },
      { t: '2026-08-19T12:05:00Z', close: 101.34 },
      { t: '2026-08-19T12:10:00Z', close: 102.5 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildHistoryResult(points))

    render(<RawDataTable ticker="AZN.L" />)

    const dataRows = screen.getAllByRole('row').slice(1) // drop header row
    expect(dataRows).toHaveLength(points.length)

    points.forEach((point, index) => {
      const row = dataRows[index]
      expect(row.textContent).toContain(point.t)
      expect(row.textContent).toContain(String(point.close))
    })
  })

  it('renders a table header row with Time and Close columns', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )

    render(<RawDataTable ticker="AZN.L" />)

    expect(screen.getByRole('columnheader', { name: /time/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /close/i })).toBeInTheDocument()
  })

  it('renders an empty/placeholder state instead of crashing when there are no points', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildHistoryResult([]))

    render(<RawDataTable ticker="AZN.L" />)

    expect(screen.getAllByRole('row')).toHaveLength(1) // header row only, no data rows
    expect(screen.getByTestId('raw-data-table').textContent).toMatch(/no (data|history)/i)
  })
})

describe('RawDataTable query isolation', () => {
  it('only uses useStockHistoryQuery — the detail-price hook is never invoked', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )

    render(<RawDataTable ticker="AZN.L" />)

    expect(mockedUseStockHistoryQuery).toHaveBeenCalledWith('AZN.L')
    expect(mockedUseStockDetailQuery).not.toHaveBeenCalled()
  })
})

/**
 * v3 error UX: stale history greys the raw-data card and keeps the last
 * received rows in place, with no error text taking the table's place.
 */
describe('RawDataTable degraded state', () => {
  function buildStaleResult(
    points: HistoryPoint[],
    error: string | null = null,
  ): UseQueryResult<HistoryResponse, Error> {
    return {
      data: {
        ticker: 'AZN.L',
        interval: '5m',
        range: '1d',
        points,
        is_stale: true,
        error,
      },
      isSuccess: true,
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as UseQueryResult<HistoryResponse, Error>
  }

  it('is not greyed while the history is fresh', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )

    render(<RawDataTable ticker="AZN.L" />)

    expect(screen.getByTestId('raw-data-table').classList.contains('is-stale')).toBe(false)
  })

  it('greys the card and keeps the last rows when the history is stale', () => {
    const points: HistoryPoint[] = [
      { t: '2026-08-19T12:00:00Z', close: 100.12 },
      { t: '2026-08-19T12:05:00Z', close: 101.34 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildStaleResult(points, 'rate limited'))

    render(<RawDataTable ticker="AZN.L" />)

    const card = screen.getByTestId('raw-data-table')
    expect(card.classList.contains('is-stale')).toBe(true)
    expect(screen.getAllByRole('row').slice(1)).toHaveLength(points.length)
    expect(card.textContent).not.toContain('rate limited')
  })
})
