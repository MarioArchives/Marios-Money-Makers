import type { RawDataTableProps } from './RawDataTable.props'
import { useStockHistoryQuery } from '../../../api/queries'
import './RawDataTable.css'

/**
 * Raw history table for the stock detail page, shown below `StockChart`.
 * Consumes the same `useStockHistoryQuery(ticker)` cache entry as the chart
 * (same query key, per plan) so it never triggers an extra network fetch.
 * Polls only the history query (never the detail/price query).
 *
 * When the history is stale/errored the last received rows stay in place
 * and the card greys out — no error text takes the table's place.
 */
export function RawDataTable({ ticker }: RawDataTableProps): JSX.Element {
  const { data, isError } = useStockHistoryQuery(ticker)
  const points = data?.points ?? []
  const isDegraded = Boolean(data?.is_stale) || Boolean(data?.error) || Boolean(isError)

  return (
    <section
      className={`raw-data-table card${isDegraded ? ' is-stale' : ''}`}
      data-testid="raw-data-table"
      aria-label="Raw history"
    >
      <header className="raw-data-table__head">
        <span className="eyebrow">Raw history</span>
      </header>
      <div className="raw-data-table__scroll">
        <table className="raw-data-table__table">
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Close</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.t}>
                <td className="numeral">{point.t}</td>
                <td className="numeral raw-data-table__close">{point.close}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {points.length === 0 && <p className="raw-data-table__empty">No history data yet.</p>}
    </section>
  )
}
