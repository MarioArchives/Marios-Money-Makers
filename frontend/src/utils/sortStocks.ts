import type { StockSummary } from '../api/types'

/** Leaderboard order: highest `change_percent` first; null (e.g. DB-fallback rows) sinks to the bottom; ties fall back to ticker order so the board never jitters. Non-mutating, same object references, so `React.memo`'d rows skip re-rendering. */
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
