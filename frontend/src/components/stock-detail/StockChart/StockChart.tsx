import { useEffect, useRef, useState } from 'react'
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
import type { HistoryPoint } from '../../../api/types'
import './StockChart.css'

function mergePoints(prev: HistoryPoint[], incoming: HistoryPoint[]): HistoryPoint[] {
  const seen = new Set(prev.map((point) => point.t))
  const appended = incoming.filter((point) => !seen.has(point.t))
  if (appended.length === 0) {
    return prev
  }
  return [...prev, ...appended]
}

/** ISO timestamp -> "14:35", so the axis reads as a trading clock. */
function formatTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
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
 * When the history is stale/errored (or the query itself is failing), the
 * accumulated series stays exactly where it is and the card greys out —
 * the line is never cleared and never replaced by an error panel.
 */
export function StockChart({ ticker }: StockChartProps): JSX.Element {
  const query = useStockHistoryQuery(ticker)
  const [points, setPoints] = useState<HistoryPoint[]>(() => query.data?.points ?? [])
  const tickerRef = useRef(ticker)
  const isDegraded =
    Boolean(query.data?.is_stale) || Boolean(query.data?.error) || Boolean(query.isError)

  useEffect(() => {
    if (tickerRef.current !== ticker) {
      tickerRef.current = ticker
      setPoints(query.data?.points ?? [])
      return
    }
    const incoming = query.data?.points
    if (!incoming) {
      return
    }
    setPoints((prev) => mergePoints(prev, incoming))
  }, [ticker, query.data])

  return (
    <section
      className={`stock-chart card${isDegraded ? ' is-stale' : ''}`}
      data-testid="stock-chart"
      aria-label="Price today"
    >
      <header className="stock-chart__head">
        <span className="eyebrow">Price today</span>
        <span className="eyebrow stock-chart__interval">
          {query.data?.interval ?? ''} {query.data?.range ?? ''}
        </span>
      </header>
      <div className="stock-chart__plot">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
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
              width={56}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 11, fill: 'var(--color-text-faint)' }}
            />
            <Tooltip
              labelFormatter={formatTime}
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
