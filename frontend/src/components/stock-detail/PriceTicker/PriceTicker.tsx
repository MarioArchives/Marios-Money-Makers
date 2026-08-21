import { memo } from 'react'
import type { PriceTickerProps } from './PriceTicker.props'
import { useStockDetailQuery } from '../../../api/queries'
import { ChangeIndicator } from '../../shared/ChangeIndicator/ChangeIndicator'
import { ErrorBadge } from '../../shared/ErrorBadge/ErrorBadge'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import { formatDisplayPrice } from '../../../utils/currency'
import './PriceTicker.css'

/**
 * Live price/change display for the stock detail page. Polls only
 * `useStockDetailQuery` (never the history query) so a chart-only poll
 * update never re-renders this component and vice versa.
 *
 * Degradation matches the leaderboard: when the figure is stale, errored,
 * or the query itself is failing, the last known price stays on screen and
 * simply greys out, marked by one muted dot. An em dash only when there has
 * never been a price to show.
 *
 * The figure is displayed in GBP at the shared `FxRateProvider` rate
 * (native currency until a rate is available), matching the leaderboard.
 *
 * Wrapped in `React.memo`: since this component receives only `ticker` as
 * a prop (all price data comes from its own query hook), memoizing it
 * means a parent re-render with the same `ticker` never re-renders this
 * component for reasons unrelated to its own query data changing.
 */
function PriceTickerComponent({ ticker }: PriceTickerProps): JSX.Element {
  const { data, isError } = useStockDetailQuery(ticker)
  const { rate } = useFxRate()
  const isDegraded = Boolean(data?.is_stale) || Boolean(data?.error) || Boolean(isError)

  return (
    <div
      className={`price-ticker${isDegraded ? ' is-stale' : ''}`}
      data-testid="price-ticker"
    >
      <span className="price-ticker__price numeral">
        {data && data.price !== null ? formatDisplayPrice(data.price, data.currency, rate) : '—'}
      </span>
      <span className="price-ticker__stats">
        <ChangeIndicator changePercent={data?.change_percent ?? null} />
        <ErrorBadge isStale={Boolean(data?.is_stale) || Boolean(isError)} error={data?.error} />
      </span>
    </div>
  )
}

export const PriceTicker = memo(PriceTickerComponent)
