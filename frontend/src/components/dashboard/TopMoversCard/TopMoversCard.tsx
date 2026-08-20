import type { TopMoversCardProps } from './TopMoversCard.props'
import './TopMoversCard.css'

/**
 * Placeholder dashboard card. Per the plan, `DashboardPage` makes no
 * network calls — this card is fully static, hardcoded content by design,
 * not a skeleton awaiting real data wiring.
 */
export function TopMoversCard(_props: TopMoversCardProps): JSX.Element {
  void _props
  return (
    <section className="dashboard-card top-movers-card" aria-label="Top movers">
      <h2>Top Movers</h2>
      <span className="dashboard-card-placeholder">Coming soon</span>
      <p className="dashboard-card-description">
        The biggest gainers and losers on the tracked leaderboard, by change percent, will
        live here.
      </p>
    </section>
  )
}
