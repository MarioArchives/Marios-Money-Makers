import { useMemo } from 'react'
import type { LeaderboardPageProps } from './LeaderboardPage.props'
import { useStocksQuery } from '../../api/queries'
import { StockTable } from '../../components/leaderboard/StockTable/StockTable'
import { ConnectionBanner } from '../../components/shared/ConnectionBanner/ConnectionBanner'
import { FxRateNote } from '../../components/shared/FxRateNote/FxRateNote'
import { MarketStatusBanner } from '../../components/shared/MarketStatusBanner/MarketStatusBanner'
import { sortByChangeDesc } from '../../utils/sortStocks'
import './LeaderboardPage.css'

/** Home page: the leaderboard, ranked by `sortByChangeDesc` and re-ranked on every poll (`StockTable` animates the moves). On query error, React Query keeps last-good `data` so the board stays on screen (greyed) behind one `ConnectionBanner`; only an empty cache falls back to an empty state, never a spinner. */
export function LeaderboardPage(_props: LeaderboardPageProps): JSX.Element {
  void _props
  const { data, isLoading, isError } = useStocksQuery()
  const stocks = useMemo(() => sortByChangeDesc(data?.stocks ?? []), [data])

  if (isError && stocks.length === 0) {
    return (
      <div className="leaderboard-page">
        <MarketStatusBanner />
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
      <MarketStatusBanner />
      {isError && <ConnectionBanner />}
      <div className="leaderboard-page__toolbar">
        <FxRateNote />
      </div>
      <StockTable stocks={stocks} isDegraded={isError} />
    </div>
  )
}
