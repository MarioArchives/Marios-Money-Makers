import { memo } from 'react'
import type { PriceTickerProps } from './PriceTicker.props'
import { useStockDetailQuery } from '../../../api/queries'
import { ChangeIndicator } from '../../shared/ChangeIndicator/ChangeIndicator'
import { ErrorBadge } from '../../shared/ErrorBadge/ErrorBadge'
import { useFxRate } from '../../../providers/FxRateProvider/FxRateProvider'
import { formatDisplayPrice } from '../../../utils/currency'
import './PriceTicker.css'

/**
 * Live GBP-converted price/change for the stock detail page. Polls only
 * `useStockDetailQuery`, never the history query — see ARCHITECTURE.md for
 * the re-render isolation this depends on. Degradation matches the leaderboard.
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
