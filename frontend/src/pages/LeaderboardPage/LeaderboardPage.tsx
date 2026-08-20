import type { LeaderboardPageProps } from './LeaderboardPage.props'
import { useStocksQuery } from '../../api/queries'
import { StockTable } from '../../components/leaderboard/StockTable/StockTable'
import { ConnectionBanner } from '../../components/shared/ConnectionBanner/ConnectionBanner'
import './LeaderboardPage.css'

/**
 * Home page: the leaderboard of all tracked UK stocks.
 *
 * Minimal page, per convention — the only thing it owns is the
 * `useStocksQuery` call.
 *
 * Failure behaviour (v3): when the query errors, React Query keeps the last
 * successful `data` for this key, so the board stays on screen, greyed, and
 * the page shows one calm `ConnectionBanner` instead of tearing the table
 * down. Per-stock staleness is a separate, quieter signal handled by
 * `StockRow`. Only when nothing has ever loaded does the page fall back to
 * an empty state — never a spinner that spins forever.
 */
export function LeaderboardPage(_props: LeaderboardPageProps): JSX.Element {
  void _props
  const { data, isLoading, isError } = useStocksQuery()
  const stocks = data?.stocks ?? []

  if (isError && stocks.length === 0) {
    return (
      <div className="leaderboard-page">
        <ConnectionBanner />
        <p className="leaderboard-page__empty">No figures received yet.</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="leaderboard-page">
        <p className="leaderboard-page__loading">Loading stocks…</p>
      </div>
    )
  }

  return (
    <div className="leaderboard-page">
      {isError && <ConnectionBanner />}
      <StockTable stocks={stocks} isDegraded={isError} />
    </div>
  )
}
