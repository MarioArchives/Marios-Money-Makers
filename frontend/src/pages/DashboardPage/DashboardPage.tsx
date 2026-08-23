import type { DashboardPageProps } from './DashboardPage.props'
import { MarketSummaryCard } from '../../components/dashboard/MarketSummaryCard/MarketSummaryCard'
import { SectorBreakdownCard } from '../../components/dashboard/SectorBreakdownCard/SectorBreakdownCard'
import { TopMoversCard } from '../../components/dashboard/TopMoversCard/TopMoversCard'
import './DashboardPage.css'

/** Placeholder market dashboard: static composition of placeholder cards, no network calls or business logic. */
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
