import { useEffect, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { StockChart, makeTimeFormatter } from './StockChart'
import { useStockHistoryQuery } from '../../../api/queries'
import type { HistoryPoint, HistoryRange, HistoryResponse } from '../../../api/types'
import { FxRateContext } from '../../../providers/FxRateProvider/FxRateProvider'
import {
  GAP_BLOCK_MIN_SLOTS,
  MARKET_CLOSED_LABEL,
  buildIntradaySeries,
  isGapKey,
} from '../../../utils/intradaySeries'

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
  XAxis: ({ dataKey, type, scale }: { dataKey?: unknown; type?: string; scale?: unknown }) => (
    <div
      data-testid="x-axis"
      data-key={typeof dataKey === 'string' ? dataKey : 'fn'}
      data-type={type ?? 'category'}
      data-scale={typeof scale === 'string' ? scale : ''}
    />
  ),
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  Tooltip: () => <div data-testid="tooltip" />,
  ReferenceArea: ({ x1, x2, label }: { x1?: number | string; x2?: number | string; label?: unknown }) => {
    const text =
      label && typeof label === 'object' && 'value' in label
        ? String((label as { value: unknown }).value)
        : typeof label === 'string'
          ? label
          : ''
    return <div data-testid="reference-area" data-x1={String(x1)} data-x2={String(x2)} data-label={text} />
  },
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

/** Exactly what the LineChart received (1d rows may include null placeholder columns). */
function getRawPlotted(): Array<{ t: string; close: number | null }> {
  const el = screen.getByTestId('line-chart')
  return JSON.parse(el.getAttribute('data-points') ?? '[]') as Array<{
    t: string
    close: number | null
  }>
}

/** The real history points on the chart, as `{t, close}` — placeholder
 * columns (the greyed market-closed block on the intraday chart) removed. */
function getRenderedPoints(): HistoryPoint[] {
  return getRawPlotted()
    .filter((p): p is { t: string; close: number } => p.close !== null)
    .map(({ t, close }) => ({ t, close }))
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

/**
 * Intraday (1d) market-closed blocks: the minute chart keeps its categorical
 * axis, and a real hole in the data that falls in a market-closed period is
 * filled with a fixed-width block of null placeholder columns, greyed out by
 * a `ReferenceArea` carrying the "no data" message. 30d / all get no blocks.
 */
describe('StockChart market-closed blocks', () => {
  // August 2026: New York is EDT, so the 09:30-16:00 ET session is 13:30Z-20:00Z.
  // 15-minute cadence around the one overnight hole: every other gap is
  // under MIN_HOLE_MS, so exactly that hole gets the block.
  const WED_1500 = '2026-08-19T19:00:00Z'
  const WED_1545 = '2026-08-19T19:45:00Z'
  const WED_1600 = '2026-08-19T20:00:00Z'
  const THU_0930 = '2026-08-20T13:30:00Z'
  const THU_0945 = '2026-08-20T13:45:00Z'
  const overnight: HistoryPoint[] = [
    { t: WED_1545, close: 100 },
    { t: WED_1600, close: 101 },
    { t: THU_0930, close: 102 },
    { t: THU_0945, close: 103 },
  ]

  function buildRangeResult(
    points: HistoryPoint[],
    range: HistoryRange,
  ): UseQueryResult<HistoryResponse, Error> {
    return {
      data: {
        ticker: 'AZN.L',
        interval: range === '1d' ? '1m' : range === '30d' ? '1h' : '1d',
        range,
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

  function getBlocks(): Array<{ x1: string; x2: string; label: string }> {
    return screen.queryAllByTestId('reference-area').map((el) => ({
      x1: el.getAttribute('data-x1') ?? '',
      x2: el.getAttribute('data-x2') ?? '',
      label: el.getAttribute('data-label') ?? '',
    }))
  }

  it('keeps the categorical axis on 1d', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildRangeResult(overnight, '1d'))
    render(<StockChart ticker="AZN.L" range="1d" />)

    const axis = screen.getByTestId('x-axis')
    expect(axis.getAttribute('data-key')).toBe('t')
    expect(axis.getAttribute('data-type')).toBe('category')
  })

  it('fills the overnight hole with null placeholder columns and one labelled block over them', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildRangeResult(overnight, '1d'))
    render(<StockChart ticker="AZN.L" range="1d" />)

    const raw = getRawPlotted()
    const gaps = raw.filter((p) => isGapKey(p.t))
    expect(gaps.length).toBeGreaterThanOrEqual(GAP_BLOCK_MIN_SLOTS)
    expect(gaps.every((p) => p.close === null)).toBe(true)
    // Placeholders sit between the two bars that bracket the hole.
    expect(raw.map((p) => (isGapKey(p.t) ? null : p.close))).toEqual([
      100,
      101,
      ...Array<null>(gaps.length).fill(null),
      102,
      103,
    ])
    expect(getBlocks()).toEqual([
      { x1: gaps[0].t, x2: gaps[gaps.length - 1].t, label: MARKET_CLOSED_LABEL },
    ])
    // ...while the accumulated/rendered real points are untouched.
    expect(getRenderedPoints()).toEqual(overnight)
  })

  it('renders no block for the ordinary bar cadence inside a session', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildRangeResult(
        [
          { t: WED_1500, close: 100 },
          { t: '2026-08-19T19:15:00Z', close: 101 },
        ],
        '1d',
      ),
    )
    render(<StockChart ticker="AZN.L" range="1d" />)

    expect(getBlocks()).toEqual([])
    expect(getRawPlotted()).toEqual([
      { t: WED_1500, close: 100 },
      { t: '2026-08-19T19:15:00Z', close: 101 },
    ])
  })

  it('converts real closes to GBP but leaves placeholder columns null', () => {
    const rate = { base: 'USD', quote: 'GBP', rate: 0.5, date: '2026-08-20', source: 'ECB' } as const
    mockedUseStockHistoryQuery.mockReturnValue(buildRangeResult(overnight, '1d'))
    render(
      <FxRateContext.Provider value={{ status: 'ready', rate }}>
        <StockChart ticker="AZN.L" range="1d" />
      </FxRateContext.Provider>,
    )

    const raw = getRawPlotted()
    const slots = raw.filter((p) => isGapKey(p.t)).length
    expect(raw.map((p) => p.close)).toEqual([50, 50.5, ...Array<null>(slots).fill(null), 51, 51.5])
  })

  it('keeps accumulating polled points and never remounts the chart on 1d', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildRangeResult(overnight.slice(0, 2), '1d'))
    const { rerender } = render(<StockChart ticker="AZN.L" range="1d" />)
    expect(getBlocks()).toEqual([])

    mockedUseStockHistoryQuery.mockReturnValue(buildRangeResult(overnight.slice(1), '1d'))
    rerender(<StockChart ticker="AZN.L" range="1d" />)

    expect(getRenderedPoints()).toEqual(overnight)
    expect(getBlocks()).toHaveLength(1)
    expect(mountTracker.count).toBe(1)
  })

  it.each(['30d', 'all'] as const)('draws no blocks on %s', (range) => {
    // Points straddling a weekend: on the daily/hourly views that is just
    // absent bars, not a greyed period.
    const points: HistoryPoint[] = [
      { t: '2026-08-21T19:00:00Z', close: 100 },
      { t: '2026-08-24T14:00:00Z', close: 101 },
    ]
    mockedUseStockHistoryQuery.mockReturnValue(buildRangeResult(points, range))
    render(<StockChart ticker="AZN.L" range={range} />)

    const axis = screen.getByTestId('x-axis')
    expect(axis.getAttribute('data-key')).toBe('t')
    expect(axis.getAttribute('data-type')).toBe('category')
    expect(getBlocks()).toEqual([])
    // The data reaching recharts is exactly the points: no placeholders.
    expect(getRawPlotted()).toEqual(points)
  })
})

describe('makeTimeFormatter', () => {
  it('formats a numeric ms value (time axis) and its ISO string (category axis) identically', () => {
    const format = makeTimeFormatter('1d')
    const iso = '2026-08-19T19:00:00Z'
    expect(format(Date.parse(iso))).toBe(format(iso))
    expect(format(iso)).toMatch(/^\d{2}:\d{2}$/)
  })

  it('falls back to the raw input when it cannot be parsed', () => {
    expect(makeTimeFormatter('1d')('not-a-date')).toBe('not-a-date')
    expect(makeTimeFormatter('30d')(Number.NaN)).toBe(Number.NaN)
  })

  it('renders a placeholder (gap) column as an empty tick label', () => {
    const out = buildIntradaySeries([
      { t: '2026-08-19T20:00:00Z', close: 101 },
      { t: '2026-08-20T13:30:00Z', close: 102 },
    ])
    const gap = out.data.find((p) => isGapKey(p.t))
    expect(gap).toBeDefined()
    expect(makeTimeFormatter('1d')(gap!.t)).toBe('')
  })
})

describe('StockChart empty state', () => {
  function buildResult(
    overrides: Partial<{
      points: HistoryPoint[] | undefined
      isLoading: boolean
      isError: boolean
      range: HistoryRange
    }> = {},
  ): UseQueryResult<HistoryResponse, Error> {
    const { points, isLoading = false, isError = false, range = '1d' } = overrides
    return {
      data:
        points === undefined
          ? undefined
          : {
              ticker: 'AZN.L',
              interval: range === '1d' ? '1m' : '1d',
              range,
              points,
              is_stale: false,
              error: null,
            },
      isSuccess: points !== undefined,
      isLoading,
      isError,
      error: isError ? new Error('boom') : null,
    } as unknown as UseQueryResult<HistoryResponse, Error>
  }

  it('explains an empty 1d window as the market being closed', () => {
    // The 1d window is the last 24h; out of hours (and all weekend, once
    // the 24h minute retention has pruned the last session) it is legitimately
    // empty -- and with no bars there is no gap to grey out either.
    mockedUseStockHistoryQuery.mockReturnValue(buildResult({ points: [] }))

    render(<StockChart ticker="AZN.L" range="1d" />)

    expect(screen.getByTestId('stock-chart-empty')).toHaveTextContent(
      'Market closed · no trades in the last 24 hours',
    )
    expect(screen.queryByTestId('line-chart')).not.toBeInTheDocument()
  })

  it('renders the chart, not the empty copy, as soon as the series has a point', () => {
    mockedUseStockHistoryQuery.mockReturnValue(
      buildResult({ points: [{ t: '2026-08-19T12:00:00Z', close: 100 }] }),
    )

    render(<StockChart ticker="AZN.L" range="1d" />)

    expect(screen.queryByTestId('stock-chart-empty')).not.toBeInTheDocument()
    expect(screen.getByTestId('line-chart')).toBeInTheDocument()
  })

  it('uses range-specific copy: an empty 30d or all-time series is not "market closed"', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildResult({ points: [], range: '30d' }))
    const { rerender } = render(<StockChart ticker="AZN.L" range="30d" />)
    expect(screen.getByTestId('stock-chart-empty')).toHaveTextContent(
      'No price history stored for the last 30 days yet',
    )

    mockedUseStockHistoryQuery.mockReturnValue(buildResult({ points: [], range: 'all' }))
    rerender(<StockChart ticker="AZN.L" range="all" />)
    expect(screen.getByTestId('stock-chart-empty')).toHaveTextContent(
      'No price history stored yet',
    )
  })

  it('stays quiet while the first fetch is still in flight', () => {
    // "No data yet" and "no data at all" are different things: only claim
    // the market is closed once the API has actually answered.
    mockedUseStockHistoryQuery.mockReturnValue(buildResult({ points: undefined, isLoading: true }))

    render(<StockChart ticker="AZN.L" range="1d" />)

    expect(screen.queryByTestId('stock-chart-empty')).not.toBeInTheDocument()
  })

  it('says the history is unavailable when the query failed with nothing cached', () => {
    mockedUseStockHistoryQuery.mockReturnValue(buildResult({ points: undefined, isError: true }))

    render(<StockChart ticker="AZN.L" range="1d" />)

    expect(screen.getByTestId('stock-chart-empty')).toHaveTextContent(
      'Price history unavailable',
    )
    // A failure is a degraded card, not a closed market.
    expect(screen.getByTestId('stock-chart')).toHaveClass('is-stale')
  })
})
