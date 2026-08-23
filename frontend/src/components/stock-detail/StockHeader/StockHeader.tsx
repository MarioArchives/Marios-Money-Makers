import { memo } from 'react'
import type { StockHeaderProps } from './StockHeader.props'
import { CompanyIcon } from '../../shared/CompanyIcon/CompanyIcon'
import './StockHeader.css'

/**
 * Static per-stock detail page header: company icon, name, sector and
 * ticker. Purely presentational — no data-fetching of its own, receives
 * everything it needs via props. Memoised so a parent re-render with the
 * same identity props costs nothing.
 */
function StockHeaderComponent({ ticker, name, sector }: StockHeaderProps): JSX.Element {
  return (
    <div className="stock-header">
      <CompanyIcon ticker={ticker} name={name} size={48} />
      <div className="stock-header__meta">
        <div className="stock-header__name">{name}</div>
        <div className="stock-header__sector">{sector}</div>
        <div className="stock-header__ticker">{ticker}</div>
      </div>
    </div>
  )
}

export const StockHeader = memo(StockHeaderComponent)
