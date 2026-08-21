import type { StockSummary } from '../api/types'

/**
 * Leaderboard order: highest `change_percent` first. Stocks with no change
 * figure yet (null — e.g. DB-fallback rows during an Alpaca outage) sink
 * to the bottom; ties (and the null group) fall back to ticker order so
 * the board is deterministic and never jitters between polls for rows
 * that did not actually change.
 *
 * Pure and non-mutating: returns a new array holding the same
 * `StockSummary` object references, so `React.memo`'d rows still skip
 * re-rendering when their own data is unchanged.
 */
export function sortByChangeDesc(stocks: readonly StockSummary[]): StockSummary[] {
  return [...stocks].sort((a, b) => {
    const aNull = a.change_percent === null
    const bNull = b.change_percent === null
    if (aNull !== bNull) {
      return aNull ? 1 : -1
    }
    if (!aNull && !bNull && a.change_percent !== b.change_percent) {
      return (b.change_percent as number) - (a.change_percent as number)
    }
    return a.ticker.localeCompare(b.ticker)
  })
}
