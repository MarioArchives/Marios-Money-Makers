import { useMemo } from 'react'
import type { LeaderboardPageProps } from './LeaderboardPage.props'
import { useStocksQuery } from '../../api/queries'
import { StockTable } from '../../components/leaderboard/StockTable/StockTable'
import { ConnectionBanner } from '../../components/shared/ConnectionBanner/ConnectionBanner'
import { FxRateNote } from '../../components/shared/FxRateNote/FxRateNote'
import { MarketStatusBanner } from '../../components/shared/MarketStatusBanner/MarketStatusBanner'
import { sortByChangeDesc } from '../../utils/sortStocks'
import './LeaderboardPage.css'

/**
 * Home page: the leaderboard of all tracked stocks.
 *
 * Minimal page, per convention — the only things it owns are the
 * `useStocksQuery` call and the board's order: rows are ranked by today's
 * change (`sortByChangeDesc`, highest first) and re-ranked on every poll,
 * so the table visibly re-sorts as the market moves (`StockTable` animates
 * the moves). The `FxRateNote` above the board discloses the USD->GBP rate
 * behind each £ figure, and the `MarketStatusBanner` at the very top says
 * whether the US market is trading and counts down to the next open/close
 * — the reason the figures sit still outside 09:30-16:00 ET.
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
