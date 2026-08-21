import { useEffect, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { StockChart } from './StockChart'
import { useStockHistoryQuery } from '../../../api/queries'
import type { HistoryPoint, HistoryResponse } from '../../../api/types'
import { FxRateContext } from '../../../providers/FxRateProvider/FxRateProvider'

vi.mock('../../../api/queries', () => ({
  useStockHistoryQuery: vi.fn(),
}))

// recharts renders SVG that jsdom lays out with zero width/height (no real
// layout engine), so ResponsiveContainer/LineChart render nothing useful
// there. Mock recharts with plain DOM stand-ins that surface the props
// StockChart passes them (via data attributes) so assertions stay
// DOM-structural instead of pixel/SVG based. The mocked LineChart also
// tracks its own mount count via a module-level counter (`vi.hoisted`),
// giving a direct signal for "did the chart get remounted".
const { mountTracker } = vi.hoisted(() => ({ mountTracker: { count: 0 } }))

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ data, children }: { data: unknown; children: ReactNode }) => {
    useEffect(() => {
      mountTracker.count += 1
    }, [])
    return (
      <div data-testid="line-chart" data-points={JSON.stringify(data)}>
        {children}
      </div>
    )
  },
  Line: ({ dataKey, isAnimationActive }: { dataKey: string; isAnimationActive: boolean }) => (
    <div data-testid="line" data-key={dataKey} data-is-animation-active={String(isAnimationActive)} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
}))

const mockedUseStockHistoryQuery = vi.mocked(useStockHistoryQuery)

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

function getRenderedPoints(): HistoryPoint[] {
  const el = screen.getByTestId('line-chart')
  return JSON.parse(el.getAttribute('data-points') ?? '[]') as HistoryPoint[]
}

beforeEach(() => {
  mockedUseStockHistoryQuery.mockReset()
  mountTracker.count = 0
})

describe('StockChart point accumulation', () => {
  it('appends and dedupes newly-polled points into local state instead of replacing it', () => {
    const initialPoints: HistoryPoint[] = [
      { t: '2026-08-19T12:00:00Z', close: 100 },
      { t: '2026-08-19T12:05:00Z', close: 101 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildHistoryResult(initialPoints))

    const { rerender } = render(<StockChart ticker="AZN.L" />)
    expect(getRenderedPoints()).toEqual(initialPoints)

    // Next poll: overlaps the last known timestamp (identical value) and
    // adds two genuinely new points.
    const polledPoints: HistoryPoint[] = [
      { t: '2026-08-19T12:05:00Z', close: 101 },
      { t: '2026-08-19T12:10:00Z', close: 102 },
      { t: '2026-08-19T12:15:00Z', close: 103 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildHistoryResult(polledPoints))
    rerender(<StockChart ticker="AZN.L" />)

    expect(getRenderedPoints()).toEqual([
      { t: '2026-08-19T12:00:00Z', close: 100 },
      { t: '2026-08-19T12:05:00Z', close: 101 },
      { t: '2026-08-19T12:10:00Z', close: 102 },
      { t: '2026-08-19T12:15:00Z', close: 103 },
    ])
  })

  it('keeps accumulating across more than one subsequent poll', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )
    const { rerender } = render(<StockChart ticker="AZN.L" />)

    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
      ]),
    )
    rerender(<StockChart ticker="AZN.L" />)

    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:05:00Z', close: 101 },
        { t: '2026-08-19T12:10:00Z', close: 102 },
      ]),
    )
    rerender(<StockChart ticker="AZN.L" />)

    expect(getRenderedPoints()).toEqual([
      { t: '2026-08-19T12:00:00Z', close: 100 },
      { t: '2026-08-19T12:05:00Z', close: 101 },
      { t: '2026-08-19T12:10:00Z', close: 102 },
    ])
  })
})

describe('StockChart mount stability across polls', () => {
  it('does not remount (root DOM node identity stable) across successive polls for the same ticker', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )
    const { rerender } = render(<StockChart ticker="AZN.L" />)
    const rootAfterMount = screen.getByTestId('stock-chart')

    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
      ]),
    )
    rerender(<StockChart ticker="AZN.L" />)
    expect(screen.getByTestId('stock-chart')).toBe(rootAfterMount)

    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
        { t: '2026-08-19T12:10:00Z', close: 102 },
      ]),
    )
    rerender(<StockChart ticker="AZN.L" />)
    expect(screen.getByTestId('stock-chart')).toBe(rootAfterMount)
  })

  it('mounts the underlying recharts LineChart exactly once across successive polls', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )
    const { rerender } = render(<StockChart ticker="AZN.L" />)
    expect(mountTracker.count).toBe(1)

    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
      ]),
    )
    rerender(<StockChart ticker="AZN.L" />)
    expect(mountTracker.count).toBe(1)

    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
        { t: '2026-08-19T12:10:00Z', close: 102 },
      ]),
    )
    rerender(<StockChart ticker="AZN.L" />)
    expect(mountTracker.count).toBe(1)
  })
})

describe('StockChart range handling', () => {
  it('requests the intraday range by default and forwards an explicit range to the history query', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )
    const { rerender } = render(<StockChart ticker="AZN.L" />)
    expect(mockedUseStockHistoryQuery).toHaveBeenLastCalledWith('AZN.L', '1d')

    rerender(<StockChart ticker="AZN.L" range="30d" />)
    expect(mockedUseStockHistoryQuery).toHaveBeenLastCalledWith('AZN.L', '30d')
  })

  it('replaces the accumulated series (instead of appending) when the range changes', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
      ]),
    )
    const { rerender } = render(<StockChart ticker="AZN.L" range="1d" />)

    const monthly = [
      { t: '2026-07-20T00:00:00Z', close: 90 },
      { t: '2026-08-19T00:00:00Z', close: 99 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildHistoryResult(monthly))
    rerender(<StockChart ticker="AZN.L" range="30d" />)

    expect(getRenderedPoints()).toEqual(monthly)
  })

  it('titles the card after the selected range', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildHistoryResult([]))
    render(<StockChart ticker="AZN.L" range="all" />)

    expect(screen.getByTestId('stock-chart').textContent).toContain('all time')
  })
})

describe('StockChart recharts wiring', () => {
  it('renders the recharts Line with isAnimationActive disabled', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )
    render(<StockChart ticker="AZN.L" />)

    const line = screen.getByTestId('line')
    expect(line.getAttribute('data-is-animation-active')).toBe('false')
  })
})

/**
 * v3 error UX: stale history greys the chart card and keeps the accumulated
 * series on screen — the line is never cleared or replaced by error text.
 */
describe('StockChart degraded state', () => {
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
    render(<StockChart ticker="AZN.L" />)

    expect(screen.getByTestId('stock-chart').classList.contains('is-stale')).toBe(false)
  })

  it('greys the chart card and keeps the last points when the history is stale', () => {
    const points: HistoryPoint[] = [
      { t: '2026-08-19T12:00:00Z', close: 100 },
      { t: '2026-08-19T12:05:00Z', close: 101 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildStaleResult(points, 'rate limited'))

    render(<StockChart ticker="AZN.L" />)

    const chart = screen.getByTestId('stock-chart')
    expect(chart.classList.contains('is-stale')).toBe(true)
    expect(getRenderedPoints()).toEqual(points)
    expect(chart.textContent).not.toContain('rate limited')
  })

  it('keeps the previously accumulated series when a later poll goes stale', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )
    const { rerender } = render(<StockChart ticker="AZN.L" />)

    mockedUseStockHistoryQuery.mockReturnValue(buildStaleResult([]))
    rerender(<StockChart ticker="AZN.L" />)

    expect(screen.getByTestId('stock-chart').classList.contains('is-stale')).toBe(true)
    expect(getRenderedPoints()).toEqual([{ t: '2026-08-19T12:00:00Z', close: 100 }])
  })
})

describe('StockChart GBP conversion', () => {
  it('plots closes converted to GBP at the shared FX rate, leaving timestamps alone', () => {
    const rate = { base: 'USD', quote: 'GBP', rate: 0.5, date: '2026-08-20', source: 'ECB' } as const
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([
        { t: '2026-08-19T12:00:00Z', close: 100 },
        { t: '2026-08-19T12:05:00Z', close: 101 },
      ]),
    )

    render(
      <FxRateContext.Provider value={{ status: 'ready', rate }}>
        <StockChart ticker="AZN.L" />
      </FxRateContext.Provider>,
    )

    expect(getRenderedPoints()).toEqual([
      { t: '2026-08-19T12:00:00Z', close: 50 },
      { t: '2026-08-19T12:05:00Z', close: 50.5 },
    ])
  })

  it('plots the native figures while no rate is available', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildHistoryResult([{ t: '2026-08-19T12:00:00Z', close: 100 }]),
    )

    render(<StockChart ticker="AZN.L" />)

    expect(getRenderedPoints()).toEqual([{ t: '2026-08-19T12:00:00Z', close: 100 }])
  })
})
