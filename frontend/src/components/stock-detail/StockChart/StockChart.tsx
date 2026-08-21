import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { StockChartProps } from './StockChart.props'
import { useStockHistoryQuery } from '../../../api/queries'
import { DEFAULT_HISTORY_RANGE, type HistoryPoint, type HistoryRange } from '../../../api/types'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import { DISPLAY_CURRENCY, convertToGbp, formatCurrency } from '../../../utils/currency'
import './StockChart.css'

function mergePoints(prev: HistoryPoint[], incoming: HistoryPoint[]): HistoryPoint[] {
  const seen = new Set(prev.map((point) => point.t))
  const appended = incoming.filter((point) => !seen.has(point.t))
  if (appended.length === 0) {
    return prev
  }
  return [...prev, ...appended]
}

/** Card title per range — what the series on screen actually covers. */
export const RANGE_TITLES: Record<HistoryRange, string> = {
  '1d': 'Price today',
  '30d': 'Price, last 30 days',
  all: 'Price, all time',
}

/**
 * ISO timestamp -> axis label. Intraday reads as a trading clock
 * ("14:35"); 30 days reads as dates ("19 Aug"); all-time reads as months
 * ("Aug 2026") — the span keeps growing, so month+year stays legible at
 * any length.
 */
function makeTimeFormatter(range: HistoryRange): (value: string) => string {
  return (value: string): string => {
    const parsed = new Date(value)
    if (Number.isNaN(parsed.getTime())) {
      return value
    }
    if (range === '1d') {
      return parsed.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
    }
    if (range === '30d') {
      return parsed.toLocaleDateString('en-US', { day: 'numeric', month: 'short' })
    }
    return parsed.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
  }
}

/**
 * Live price chart for the stock detail page. Polls only
 * `useStockHistoryQuery` (never the detail query). Newly-polled points are
 * appended and deduped by timestamp (`t`) into local `HistoryPoint[]`
 * state rather than replacing the array wholesale, so the underlying
 * recharts `LineChart` gets a stable, growing dataset instead of a fresh
 * array reference on every poll — avoiding an unnecessary remount/redraw.
 * Renders `<Line isAnimationActive={false} .../>` so appended points don't
 * replay an entry animation on every 20s poll.
 *
 * Switching `ticker` or `range` is the one case where the accumulated
 * series is replaced wholesale — it is a different series.
 *
 * The plotted values are converted to GBP at the shared `FxRateProvider`
 * rate (memoised per points/rate pair); the accumulated series itself
 * stays in the API's native currency so a rate refresh never disturbs the
 * dedupe-by-timestamp state. Without a rate the native figures plot.
 *
 * When the history is stale/errored (or the query itself is failing), the
 * accumulated series stays exactly where it is and the card greys out —
 * the line is never cleared and never replaced by an error panel.
 */
export function StockChart({ ticker, range = DEFAULT_HISTORY_RANGE }: StockChartProps): JSX.Element {
  const query = useStockHistoryQuery(ticker, range)
  const [points, setPoints] = useState<HistoryPoint[]>(() => query.data?.points ?? [])
  const seriesId = `${ticker}:${range}`
  const seriesRef = useRef(seriesId)
  const isDegraded =
    Boolean(query.data?.is_stale) || Boolean(query.data?.error) || Boolean(query.isError)

  useEffect(() => {
    if (seriesRef.current !== seriesId) {
      seriesRef.current = seriesId
      setPoints(query.data?.points ?? [])
      return
    }
    const incoming = query.data?.points
    if (!incoming) {
      return
    }
    setPoints((prev) => mergePoints(prev, incoming))
  }, [seriesId, query.data])

  const formatTime = makeTimeFormatter(range)
  const { rate } = useFxRate()
  // History points carry no currency field; the API's stocks are USD.
  const nativeCurrency = 'USD'
  const displayCurrency = rate ? DISPLAY_CURRENCY : nativeCurrency
  const plotted = useMemo(
    () =>
      points.map((point) => ({
        ...point,
        close: convertToGbp(point.close, nativeCurrency, rate) ?? point.close,
      })),
    [points, rate],
  )
  const formatPrice = (value: number): string => formatCurrency(value, displayCurrency)

  return (
    <section
      className={`stock-chart card${isDegraded ? ' is-stale' : ''}`}
      data-testid="stock-chart"
      aria-label={RANGE_TITLES[range]}
    >
      <header className="stock-chart__head">
        <span className="eyebrow">{RANGE_TITLES[range]}</span>
        <span className="eyebrow stock-chart__interval">
          {query.data?.interval ?? ''} {query.data?.range ?? ''}
        </span>
      </header>
      <div className="stock-chart__plot">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={plotted} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
            <CartesianGrid stroke="var(--color-chart-grid)" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={formatTime}
              tickLine={false}
              axisLine={false}
              minTickGap={32}
              tick={{ fontSize: 11, fill: 'var(--color-text-faint)' }}
            />
            <YAxis
              domain={['auto', 'auto']}
              width={64}
              tickFormatter={formatPrice}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 11, fill: 'var(--color-text-faint)' }}
            />
            <Tooltip
              labelFormatter={formatTime}
              formatter={(value) => [formatPrice(Number(value)), displayCurrency]}
              contentStyle={{
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                borderRadius: '8px',
                fontSize: '0.8125rem',
              }}
            />
            <Line
              type="monotone"
              dataKey="close"
              isAnimationActive={false}
              dot={false}
              stroke="currentColor"
              strokeWidth={2}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
