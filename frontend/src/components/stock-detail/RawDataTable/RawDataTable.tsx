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

/** Alpaca bar fields in the stored `analytics` JSON. `unit: null` means the
 * stock's native currency, resolved at render. */
const ANALYTICS_COLUMNS: ReadonlyArray<{ key: string; label: string; unit: string | null }> = [
  { key: 'o', label: 'Open', unit: null },
  { key: 'h', label: 'High', unit: null },
  { key: 'l', label: 'Low', unit: null },
  { key: 'c', label: 'Close', unit: null },
  { key: 'v', label: 'Volume', unit: 'shares' },
  { key: 'vw', label: 'VWAP', unit: null },
  { key: 'n', label: 'Trades', unit: 'count' },
]

/** Unit is plain text inside the `<th>` so it's part of the accessible name (e.g. "Price (USD)"). */
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
 * Raw view of every stored bar for `ticker` at the picked tier (defaults to
 * the range's tier; the in-card switch overrides for that range only).
 * See ARCHITECTURE.md for the full column/tier/degradation contract.
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

/** Memoised: heaviest subtree on the page (~1,440 rows in the minute tier); must not re-render on parent updates. */
export const RawDataTable = memo(RawDataTableComponent)
