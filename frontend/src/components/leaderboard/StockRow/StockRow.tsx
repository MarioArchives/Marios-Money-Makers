import { memo } from 'react'
import { Link } from 'react-router-dom'
import type { StockRowProps } from './StockRow.props'
import { CompanyIcon } from '../../shared/CompanyIcon/CompanyIcon'
import { ChangeIndicator } from '../../shared/ChangeIndicator/ChangeIndicator'
import { ErrorBadge } from '../../shared/ErrorBadge/ErrorBadge'
import { RankDeltaChip } from '../RankDeltaChip/RankDeltaChip'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import { formatDisplayPrice } from '../../../utils/currency'
import type { FxRate } from '../../../api/types'
import './StockRow.css'

function formatPrice(price: number | null, currency: string, rate: FxRate | null): string {
  return price === null ? '—' : formatDisplayPrice(price, currency, rate)
}

function directionClass(changePercent: number | null): 'up' | 'down' | 'flat' {
  if (changePercent === null || changePercent === 0) {
    return 'flat'
  }
  return changePercent > 0 ? 'up' : 'down'
}

/**
 * A single leaderboard row, laid out as the wireframe's three-column pill:
 * name (+ icon) | price | stats. A coloured spine down the leading edge
 * carries the direction before the reader gets to the number.
 *
 * Degraded rows — `is_stale` and/or a per-stock `error` from the backend —
 * are greyed out rather than annotated: the last known price stays on
 * screen (an em dash if there has never been one), the spine drops to
 * neutral, and a single muted dot marks the row. No error text: a
 * rate-limited upstream feed is not something the reader can act on.
 *
 * Prices are displayed in GBP, converted at the shared `FxRateProvider`
 * rate (`formatDisplayPrice`); until a rate is available the native
 * figure shows instead, so the board never goes blank.
 *
 * `rankDelta` (set briefly by `StockTable` after a re-rank) adds a small
 * `▲n`/`▼n` chip after the ticker.
 *
 * Wrapped in `React.memo` so unchanged rows (same `stock` object reference,
 * per the leaderboard's structural-sharing query data, and no rank-delta
 * change) skip re-rendering when the parent `StockTable`/`LeaderboardPage`
 * re-renders.
 */
function StockRowComponent({ stock, rankDelta }: StockRowProps): JSX.Element {
  const { rate } = useFxRate()
  const isDegraded = stock.is_stale || Boolean(stock.error)
  const className = [
    'stock-row',
    `stock-row--${directionClass(stock.change_percent)}`,
    isDegraded ? 'is-stale' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <Link to={`/stock/${stock.ticker}`} className={className} data-testid="stock-row">
      <div className="stock-row__name">
        <CompanyIcon ticker={stock.ticker} name={stock.name} size={30} />
        <div className="stock-row__name-text">
          <div className="stock-row__company">{stock.name}</div>
          <div className="stock-row__ticker">
            {stock.ticker}
            <RankDeltaChip delta={rankDelta} />
          </div>
        </div>
      </div>
      <div className="stock-row__price numeral">
        {formatPrice(stock.price, stock.currency, rate)}
      </div>
      <div className="stock-row__stats">
        <ChangeIndicator changePercent={stock.change_percent} />
        <ErrorBadge isStale={stock.is_stale} error={stock.error} />
      </div>
    </Link>
  )
}

export const StockRow = memo(StockRowComponent)
