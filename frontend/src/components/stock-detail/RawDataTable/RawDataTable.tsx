import { memo, useMemo, useState } from 'react'
import type { RawDataTableProps } from './RawDataTable.props'
import { useStoredDataQuery } from '../../../api/queries'
import {
  DEFAULT_HISTORY_RANGE,
  STORED_TIERS,
  TIER_FOR_RANGE,
  type HistoryRange,
  type StoredBar,
  type StoredTier,
} from '../../../api/types'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import { DISPLAY_CURRENCY, convertToGbp, formatCurrency } from '../../../utils/currency'
import './RawDataTable.css'

/** Human labels for the SQLite tiers (`days` holds daily bars). */
export const TIER_LABELS: Record<StoredTier, string> = {
  minute: 'Minute',
  hour: 'Hourly',
  days: 'Daily',
}

/**
 * Alpaca bar fields inside the stored `analytics` JSON, in display order.
 * `unit` is the header suffix; `null` means "the stock's native currency"
 * (the response's `currency`, USD for Alpaca) and is resolved at render.
 */
const ANALYTICS_COLUMNS: ReadonlyArray<{ key: string; label: string; unit: string | null }> = [
  { key: 'o', label: 'Open', unit: null },
  { key: 'h', label: 'High', unit: null },
  { key: 'l', label: 'Low', unit: null },
  { key: 'c', label: 'Close', unit: null },
  { key: 'v', label: 'Volume', unit: 'shares' },
  { key: 'vw', label: 'VWAP', unit: null },
  { key: 'n', label: 'Trades', unit: 'count' },
]

/**
 * Column header: label plus a muted unit suffix. The unit is plain text
 * inside the `<th>` so it is part of the header's accessible name
 * (e.g. "Price (USD)").
 */
function ColumnHeader({
  label,
  unit,
  numeric = false,
}: {
  label: string
  unit: string
  numeric?: boolean
}): JSX.Element {
  return (
    <th scope="col" className={numeric ? 'raw-data-table__num' : undefined}>
      {label} <span className="raw-data-table__unit">({unit})</span>
    </th>
  )
}

function cell(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  return String(value)
}

const countFormatter = new Intl.NumberFormat('en-US')

/**
 * Raw view of everything the backend's SQLite store holds for this stock:
 * every row of one tier's `bars_<tier>` table, every column (`ts`,
 * `price`, the stored Alpaca analytics — open/high/low/close/volume/
 * VWAP/trade count — and `recorded_at`), newest first, plus per-tier row
 * counts and the tier's last successful Alpaca fetch. Backed by
 * `useStoredDataQuery(ticker, tier)`; polls only that query (never the
 * detail/price or history queries).
 *
 * The tier shown defaults to the one behind the page's history `range`
 * and follows it when the range changes; the in-card switch overrides it
 * for the current range only. The extra "Close (GBP)" column converts the
 * stored close at the shared `FxRateProvider` rate; it disappears when no
 * rate is available. The stored figures themselves are never altered —
 * this is the raw data. Every header carries its unit: the response's
 * `currency` for the stored money columns (falling back to USD), GBP for
 * the converted column, `shares`/`count` for volume/trades and UTC for the
 * timestamps.
 *
 * When the query fails, the last received rows stay in place and the card
 * greys out — no error text takes the table's place.
 */
function RawDataTableComponent({ ticker, range = DEFAULT_HISTORY_RANGE }: RawDataTableProps): JSX.Element {
  const [picked, setPicked] = useState<{ forRange: HistoryRange; tier: StoredTier } | null>(null)
  const tier = picked && picked.forRange === range ? picked.tier : TIER_FOR_RANGE[range]
  const { data, isError } = useStoredDataQuery(ticker, tier)
  const { rate } = useFxRate()
  const currency = data?.currency ?? 'USD'

  const rows = useMemo<StoredBar[]>(() => [...(data?.rows ?? [])].reverse(), [data])
  const showGbp = rate !== null && convertToGbp(1, currency, rate) !== null
  const isDegraded = Boolean(isError)

  return (
    <section
      className={`raw-data-table card${isDegraded ? ' is-stale' : ''}`}
      data-testid="raw-data-table"
      aria-label="Stored data"
    >
      <header className="raw-data-table__head">
        <div className="raw-data-table__title">
          <span className="eyebrow">Stored data</span>
          <span className="raw-data-table__meta" data-testid="raw-data-meta">
            {data?.table ?? `bars_${tier}`} · {countFormatter.format(rows.length)} rows · last Alpaca
            fetch {data?.last_fetch_at ?? 'never'}
          </span>
        </div>
        <div className="raw-data-table__tiers" role="group" aria-label="Stored tier">
          {STORED_TIERS.map((option) => {
            const selected = option === tier
            const count = data?.counts?.[option]
            return (
              <button
                key={option}
                type="button"
                className={`raw-data-table__tier${selected ? ' is-selected' : ''}`}
                aria-pressed={selected}
                onClick={() => {
                  if (!selected) setPicked({ forRange: range, tier: option })
                }}
              >
                {TIER_LABELS[option]}
                {count !== undefined && (
                  <span className="raw-data-table__tier-count">{countFormatter.format(count)}</span>
                )}
              </button>
            )
          })}
        </div>
      </header>
      <div className="raw-data-table__scroll">
        <table className="raw-data-table__table">
          <thead>
            <tr>
              <ColumnHeader label="Time" unit="UTC" />
              <ColumnHeader label="Price" unit={currency} numeric />
              {showGbp && <ColumnHeader label="Close" unit={DISPLAY_CURRENCY} numeric />}
              {ANALYTICS_COLUMNS.map((column) => (
                <ColumnHeader key={column.key} label={column.label} unit={column.unit ?? currency} numeric />
              ))}
              <ColumnHeader label="Recorded at" unit="UTC" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const gbp = showGbp ? convertToGbp(row.price, currency, rate) : null
              return (
                <tr key={row.ts}>
                  <td className="numeral">{row.ts}</td>
                  <td className="numeral raw-data-table__num raw-data-table__close">{row.price}</td>
                  {showGbp && (
                    <td className="numeral raw-data-table__num raw-data-table__close">
                      {gbp === null ? '—' : formatCurrency(gbp, DISPLAY_CURRENCY)}
                    </td>
                  )}
                  {ANALYTICS_COLUMNS.map((column) => (
                    <td key={column.key} className="numeral raw-data-table__num">
                      {cell(row.analytics[column.key])}
                    </td>
                  ))}
                  <td className="numeral">{row.recorded_at}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && (
        <p className="raw-data-table__empty">Nothing stored yet for this tier.</p>
      )}
    </section>
  )
}

/**
 * Memoised: the table is the heaviest subtree on the page (every stored
 * row, ~1 440 in the minute tier), so it must re-render only for its own
 * query data, a tier pick or a ticker/range change — never because the
 * parent page re-rendered.
 */
export const RawDataTable = memo(RawDataTableComponent)
