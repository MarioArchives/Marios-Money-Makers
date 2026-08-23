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

/**
 * The page only needs the static identity of the stock. Selecting it
 * (module-level so the selector is stable) means the page — and with it
 * every child it composes — is NOT re-rendered by the 20 s price tick;
 * `PriceTicker` owns the live figure through its own query observer.
 */
const selectIdentity = (summary: StockSummary): { name: string; sector: string } => ({
  name: summary.name,
  sector: summary.sector,
})

/**
 * Per-stock detail page. Reads `ticker` from the route params and the
 * history `range` from the URL search params (`?range=30d`, via
 * `useHistoryRange`), and composes `StockHeader` + `PriceTicker` +
 * `RangeSelector` + `StockChart` + `RawDataTable` (graph card above
 * raw-data card, per the wireframe), passing `ticker`/`range` down.
 * Holds no live data state of its own: `PriceTicker`, `StockChart` and
 * `RawDataTable` each own their own polling query independently (per the
 * plan, so a price tick never touches chart render inputs and vice versa).
 * The one query this page reads (`useStockDetailQuery`, narrowed with
 * `selectIdentity`) sources the static `name`/`sector` for `StockHeader` —
 * it shares its cache entry with `PriceTicker`'s own query, so it adds no
 * extra network traffic, and because only the selected identity is
 * subscribed to, a price tick never re-renders the page tree.
 *
 * Failure behaviour (v3): that shared query's error state is also this
 * page's signal that the backend is unreachable. Nothing is unmounted —
 * price, chart and table keep their last figures and the whole page greys
 * out behind one calm `ConnectionBanner`.
 */
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
