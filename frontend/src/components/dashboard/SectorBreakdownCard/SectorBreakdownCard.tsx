import type { SectorBreakdownCardProps } from './SectorBreakdownCard.props'
import './SectorBreakdownCard.css'

/**
 * Placeholder dashboard card. Per the plan, `DashboardPage` makes no
 * network calls — this card is fully static, hardcoded content by design,
 * not a skeleton awaiting real data wiring.
 */
export function SectorBreakdownCard(_props: SectorBreakdownCardProps): JSX.Element {
  void _props
  return (
    <section className="dashboard-card sector-breakdown-card" aria-label="Sector breakdown">
      <h2>Sector Breakdown</h2>
      <span className="dashboard-card-placeholder">Coming soon</span>
      <p className="dashboard-card-description">
        A breakdown of the tracked companies by sector (Pharma, Banking, Energy, and so on)
        will live here.
      </p>
    </section>
  )
}
