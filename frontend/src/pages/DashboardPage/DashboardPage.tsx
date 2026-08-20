import type { DashboardPageProps } from './DashboardPage.props'
import { MarketSummaryCard } from '../../components/dashboard/MarketSummaryCard/MarketSummaryCard'
import { SectorBreakdownCard } from '../../components/dashboard/SectorBreakdownCard/SectorBreakdownCard'
import { TopMoversCard } from '../../components/dashboard/TopMoversCard/TopMoversCard'
import './DashboardPage.css'

/**
 * Placeholder market dashboard page. Per the plan, this page makes no
 * network calls at all — it is a static composition of placeholder cards.
 * Minimal page, per convention: no business logic or data-fetching state
 * lives here.
 */
export function DashboardPage(_props: DashboardPageProps): JSX.Element {
  void _props
  return (
    <div className="dashboard-page">
      <MarketSummaryCard />
      <SectorBreakdownCard />
      <TopMoversCard />
    </div>
  )
}
