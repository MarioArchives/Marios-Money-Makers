import type { MarketSummaryCardProps } from './MarketSummaryCard.props'
import './MarketSummaryCard.css'

/** Placeholder card — see README.md ("dashboard page is placeholder cards only"). */
export function MarketSummaryCard(_props: MarketSummaryCardProps): JSX.Element {
  void _props
  return (
    <section className="dashboard-card market-summary-card" aria-label="Market summary">
      <h2>Market Summary</h2>
      <span className="dashboard-card-placeholder">Coming soon</span>
      <p className="dashboard-card-description">
        An at-a-glance summary of overall market movement across the tracked UK leaderboard
        will live here.
      </p>
    </section>
  )
}
