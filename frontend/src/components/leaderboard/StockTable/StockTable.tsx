import type { StockTableProps } from './StockTable.props'
import { StockRow } from '../StockRow/StockRow'
import './StockTable.css'

/**
 * Renders the full leaderboard: one `StockRow` per entry in `stocks`, in
 * the order given (the caller/query layer owns sorting).
 *
 * `isDegraded` greys the whole board at once, for when the leaderboard
 * query itself is failing and every row on screen is a last known figure.
 */
export function StockTable({ stocks, isDegraded = false }: StockTableProps): JSX.Element {
  return (
    <div
      className={`stock-table${isDegraded ? ' stock-table--degraded' : ''}`}
      data-testid="stock-table"
    >
      <div className="stock-table__head" aria-hidden="true">
        <span className="eyebrow">Company</span>
        <span className="eyebrow stock-table__head-price">Price</span>
        <span className="eyebrow stock-table__head-stats">Today</span>
      </div>
      <div className="stock-table__rows">
        {stocks.map((stock) => (
          <StockRow key={stock.ticker} stock={stock} />
        ))}
      </div>
    </div>
  )
}
