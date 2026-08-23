import { useParams } from 'react-router-dom'
import type { StockDetailPageProps } from './StockDetailPage.props'
import { StockHeader } from '../../components/stock-detail/StockHeader/StockHeader'
import { PriceTicker } from '../../components/stock-detail/PriceTicker/PriceTicker'
import { RangeSelector } from '../../components/stock-detail/RangeSelector/RangeSelector'
import { StockChart } from '../../components/stock-detail/StockChart/StockChart'
import { RawDataTable } from '../../components/stock-detail/RawDataTable/RawDataTable'
import { ConnectionBanner } from '../../components/shared/ConnectionBanner/ConnectionBanner'
import { FxRateNote } from '../../components/shared/FxRateNote/FxRateNote'
import { useStockDetailQuery } from '../../api/queries'
import type { StockSummary } from '../../api/types'
import { useHistoryRange } from '../../hooks/useHistoryRange'
import './StockDetailPage.css'

/** Static identity only, selected module-level so the selector is stable: the page (and its children) is NOT re-rendered by the 20s price tick -- `PriceTicker` owns the live figure via its own query observer. */
const selectIdentity = (summary: StockSummary): { name: string; sector: string } => ({
  name: summary.name,
  sector: summary.sector,
})

/** Per-stock detail page: composes `StockHeader`/`PriceTicker`/`RangeSelector`/`StockChart`/`RawDataTable`, each owning its own polling query so a price tick never touches chart inputs. On query error nothing unmounts -- the page greys out behind one `ConnectionBanner`. */
export function StockDetailPage(_props: StockDetailPageProps): JSX.Element {
  void _props
  const { ticker } = useParams<{ ticker: string }>()
  const tickerValue = ticker ?? ''
  const { data, isError } = useStockDetailQuery(tickerValue, selectIdentity)
  const [range, setRange] = useHistoryRange()

  return (
    <div className={`stock-detail-page${isError ? ' is-disconnected' : ''}`}>
      {isError && <ConnectionBanner />}
      <section className="card stock-detail-page__summary">
        <StockHeader ticker={tickerValue} name={data?.name ?? ''} sector={data?.sector ?? ''} />
        <PriceTicker ticker={tickerValue} />
      </section>
      <div className="stock-detail-page__toolbar">
        <FxRateNote />
        <RangeSelector value={range} onChange={setRange} />
      </div>
      <StockChart ticker={tickerValue} range={range} />
      <RawDataTable ticker={tickerValue} range={range} />
    </div>
  )
}
