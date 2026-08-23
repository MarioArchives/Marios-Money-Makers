import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { RawDataTable } from './RawDataTable'
import { useStockDetailQuery, useStockHistoryQuery, useStoredDataQuery } from '../../../api/queries'
import type { StoredBar, StoredDataResponse, StoredTier } from '../../../api/types'
import { FxRateContext } from '../../../providers/FxRateProvider/FxRateProvider'

// Static vi.mock factory with vi.fn() stand-ins, driven per-test via
// mockReturnValue / mockImplementation. The detail and history hooks are
// included purely so the isolation test can assert they are never called.
vi.mock('../../../api/queries', () => ({
  useStoredDataQuery: vi.fn(),
  useStockHistoryQuery: vi.fn(),
  useStockDetailQuery: vi.fn(),
}))

const mockedUseStoredDataQuery = vi.mocked(useStoredDataQuery)
const mockedUseStockHistoryQuery = vi.mocked(useStockHistoryQuery)
const mockedUseStockDetailQuery = vi.mocked(useStockDetailQuery)

function bar(ts: string, price: number, recordedAt = '2026-08-20T12:00:00Z'): StoredBar {
  return {
    ts,
    price,
    analytics: { o: price - 1, h: price + 1, l: price - 2, c: price, v: 1000, vw: price + 0.5, n: 10 },
    recorded_at: recordedAt,
  }
}

function buildStoredResult(
  rows: StoredBar[],
  overrides: Partial<StoredDataResponse> = {},
  queryOverrides: Partial<UseQueryResult<StoredDataResponse, Error>> = {},
): UseQueryResult<StoredDataResponse, Error> {
  return {
    data: {
      ticker: 'AAPL',
      tier: 'minute',
      table: 'bars_minute',
      currency: 'USD',
      last_fetch_at: '2026-08-20T11:59:40Z',
      counts: { minute: rows.length, hour: 7, days: 251 },
      rows,
      ...overrides,
    },
    isSuccess: true,
    isLoading: false,
    isError: false,
    error: null,
    ...queryOverrides,
  } as unknown as UseQueryResult<StoredDataResponse, Error>
}

function dataRows(): HTMLElement[] {
  return screen.getAllByRole('row').slice(1) // drop header row
}

beforeEach(() => {
  mockedUseStoredDataQuery.mockReset()
  mockedUseStockHistoryQuery.mockReset()
  mockedUseStockDetailQuery.mockReset()
})

describe('RawDataTable rendering', () => {
  it('renders every stored row newest first with every stored column', () => {
    const rows = [bar('2026-08-20T11:58:00Z', 100.12), bar('2026-08-20T11:59:00Z', 101.34)]
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult(rows))

    render(<RawDataTable ticker="AAPL" />)

    const rendered = dataRows()
    expect(rendered).toHaveLength(2)
    // Newest first.
    expect(rendered[0].textContent).toContain('2026-08-20T11:59:00Z')
    expect(rendered[1].textContent).toContain('2026-08-20T11:58:00Z')
    // Every stored column for the first row: ts, price, analytics o/h/l/c/v/vw/n, recorded_at.
    const cells = within(rendered[0])
      .getAllByRole('cell')
      .map((cell) => cell.textContent)
    expect(cells).toEqual([
      '2026-08-20T11:59:00Z',
      '101.34',
      '100.34',
      '102.34',
      '99.34',
      '101.34',
      '1000',
      '101.84',
      '10',
      '2026-08-20T12:00:00Z',
    ])
  })

  it('renders headers for time, price, the analytics fields and recorded_at', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-20T11:59:00Z', 100)]))

    render(<RawDataTable ticker="AAPL" />)

    for (const name of [/time/i, /^price/i, /open/i, /high/i, /low/i, /^close/i, /volume/i, /vwap/i, /trades/i, /recorded/i]) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument()
    }
  })

  it('shows a unit in every column header, using the response currency for money columns', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-20T11:59:00Z', 100)]))

    render(<RawDataTable ticker="AAPL" />)

    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent?.replace(/\s+/g, ' ').trim())
    expect(headers).toEqual([
      'Time (UTC)',
      'Price (USD)',
      'Open (USD)',
      'High (USD)',
      'Low (USD)',
      'Close (USD)',
      'Volume (shares)',
      'VWAP (USD)',
      'Trades (count)',
      'Recorded at (UTC)',
    ])
    // The unit is part of each header's accessible name (plain text, not aria-hidden).
    for (const name of headers) {
      expect(screen.getByRole('columnheader', { name: name as string })).toBeInTheDocument()
    }
    for (const unit of screen.getByRole('table').querySelectorAll('.raw-data-table__unit')) {
      expect(unit).not.toHaveAttribute('aria-hidden')
    }
    expect(screen.getByRole('table').querySelectorAll('.raw-data-table__unit')).toHaveLength(headers.length)
  })

  it('labels the stored money columns with whatever currency the response reports', () => {
    mockedUseStoredDataQuery.mockReturnValue(
      buildStoredResult([bar('2026-08-20T11:59:00Z', 100)], { currency: 'EUR' }),
    )

    render(<RawDataTable ticker="SAP" />)

    for (const name of [/^price \(EUR\)$/i, /^open \(EUR\)$/i, /^high \(EUR\)$/i, /^low \(EUR\)$/i, /^close \(EUR\)$/i, /^vwap \(EUR\)$/i]) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('columnheader', { name: /^volume \(shares\)$/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^trades \(count\)$/i })).toBeInTheDocument()
  })

  it('falls back to USD for the money columns when the response has no currency yet', () => {
    mockedUseStoredDataQuery.mockReturnValue(
      buildStoredResult([], {}, { data: undefined, isSuccess: false, isLoading: true }),
    )

    render(<RawDataTable ticker="AAPL" />)

    expect(screen.getByRole('columnheader', { name: /^price \(USD\)$/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^close \(USD\)$/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^time \(UTC\)$/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^recorded at \(UTC\)$/i })).toBeInTheDocument()
  })

  it('shows the table name, row count and last Alpaca fetch in the meta line', () => {
    mockedUseStoredDataQuery.mockReturnValue(
      buildStoredResult([bar('2026-08-20T11:59:00Z', 100)], { last_fetch_at: '2026-08-20T11:59:40Z' }),
    )

    render(<RawDataTable ticker="AAPL" />)

    const meta = screen.getByTestId('raw-data-meta').textContent ?? ''
    expect(meta).toContain('bars_minute')
    expect(meta).toContain('1 rows')
    expect(meta).toContain('2026-08-20T11:59:40Z')
  })

  it('renders a placeholder instead of crashing when the tier is empty', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([], { last_fetch_at: null }))

    render(<RawDataTable ticker="AAPL" />)

    expect(screen.getAllByRole('row')).toHaveLength(1) // header row only
    expect(screen.getByTestId('raw-data-table').textContent).toMatch(/nothing stored/i)
    expect(screen.getByTestId('raw-data-meta').textContent).toContain('never')
  })

  it('surfaces unreadable analytics values as an em dash rather than crashing', () => {
    mockedUseStoredDataQuery.mockReturnValue(
      buildStoredResult([{ ...bar('2026-08-20T11:59:00Z', 100), analytics: { raw: 'not json' } }]),
    )

    render(<RawDataTable ticker="AAPL" />)

    expect(within(dataRows()[0]).getAllByRole('cell')[2].textContent).toBe('—')
  })
})

describe('RawDataTable tier selection', () => {
  it('defaults the tier to the one behind the history range and offers all three with counts', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-19T00:00:00Z', 100)], { tier: 'days', table: 'bars_days' }))

    render(<RawDataTable ticker="AAPL" range="all" />)

    expect(mockedUseStoredDataQuery).toHaveBeenCalledWith('AAPL', 'days')
    const group = screen.getByRole('group', { name: /stored tier/i })
    expect(within(group).getByRole('button', { name: /minute/i })).toHaveAttribute('aria-pressed', 'false')
    expect(within(group).getByRole('button', { name: /hourly/i })).toHaveAttribute('aria-pressed', 'false')
    expect(within(group).getByRole('button', { name: /daily/i })).toHaveAttribute('aria-pressed', 'true')
    expect(within(group).getByRole('button', { name: /daily/i }).textContent).toContain('251')
  })

  it('maps 1d -> minute and 30d -> hour', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([]))

    const { unmount } = render(<RawDataTable ticker="AAPL" range="1d" />)
    expect(mockedUseStoredDataQuery).toHaveBeenLastCalledWith('AAPL', 'minute')
    unmount()

    render(<RawDataTable ticker="AAPL" range="30d" />)
    expect(mockedUseStoredDataQuery).toHaveBeenLastCalledWith('AAPL', 'hour')
  })

  it('lets the user switch tiers in the card and follows the range again when it changes', () => {
    mockedUseStoredDataQuery.mockImplementation((_ticker: string, tier: StoredTier) =>
      buildStoredResult([], { tier, table: `bars_${tier}` }),
    )

    const { rerender } = render(<RawDataTable ticker="AAPL" range="1d" />)
    fireEvent.click(screen.getByRole('button', { name: /hourly/i }))
    expect(mockedUseStoredDataQuery).toHaveBeenLastCalledWith('AAPL', 'hour')
    expect(screen.getByRole('button', { name: /hourly/i })).toHaveAttribute('aria-pressed', 'true')

    rerender(<RawDataTable ticker="AAPL" range="all" />)
    expect(mockedUseStoredDataQuery).toHaveBeenLastCalledWith('AAPL', 'days')
  })
})

describe('RawDataTable GBP column', () => {
  const rate = { base: 'USD', quote: 'GBP', rate: 0.5, date: '2026-08-20', source: 'ECB' } as const

  it('adds a Close (GBP) column converted at the shared FX rate, leaving stored figures untouched', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-20T11:59:00Z', 101.34)]))

    render(
      <FxRateContext.Provider value={{ status: 'ready', rate }}>
        <RawDataTable ticker="AAPL" />
      </FxRateContext.Provider>,
    )

    // The converted column is labelled with the currency actually shown (GBP),
    // right after the raw price column which keeps the stored currency (USD).
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent?.replace(/\s+/g, ' ').trim())
    expect(headers.slice(0, 3)).toEqual(['Time (UTC)', 'Price (USD)', 'Close (GBP)'])
    expect(screen.getByRole('columnheader', { name: /^close \(GBP\)$/i })).toBeInTheDocument()
    const cells = within(dataRows()[0]).getAllByRole('cell').map((cell) => cell.textContent)
    expect(cells[1]).toBe('101.34') // stored price, raw
    expect(cells[2]).toBe('£50.67') // converted
  })

  it('omits the GBP column while no rate is available', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-20T11:59:00Z', 101.34)]))

    render(<RawDataTable ticker="AAPL" />)

    expect(screen.queryByRole('columnheader', { name: /close \(GBP\)/i })).not.toBeInTheDocument()
  })
})

describe('RawDataTable query isolation', () => {
  it('only uses useStoredDataQuery — the detail and history hooks are never invoked', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-20T11:59:00Z', 100)]))

    render(<RawDataTable ticker="AAPL" />)

    expect(mockedUseStoredDataQuery).toHaveBeenCalledWith('AAPL', 'minute')
    expect(mockedUseStockDetailQuery).not.toHaveBeenCalled()
    expect(mockedUseStockHistoryQuery).not.toHaveBeenCalled()
  })
})

describe('RawDataTable degraded state', () => {
  it('is not greyed while the query is healthy', () => {
    mockedUseStoredDataQuery.mockReturnValue(buildStoredResult([bar('2026-08-20T11:59:00Z', 100)]))

    render(<RawDataTable ticker="AAPL" />)

    expect(screen.getByTestId('raw-data-table').classList.contains('is-stale')).toBe(false)
  })

  it('greys the card and keeps the last rows when the query is failing', () => {
    const rows = [bar('2026-08-20T11:58:00Z', 100.12), bar('2026-08-20T11:59:00Z', 101.34)]
    mockedUseStoredDataQuery.mockReturnValue(
      buildStoredResult(rows, {}, { isError: true, error: new Error('API error 503') }),
    )

    render(<RawDataTable ticker="AAPL" />)

    const card = screen.getByTestId('raw-data-table')
    expect(card.classList.contains('is-stale')).toBe(true)
    expect(dataRows()).toHaveLength(rows.length)
    expect(card.textContent).not.toContain('503')
  })
})
