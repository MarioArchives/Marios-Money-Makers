// Minimal page — no props of its own; it owns the useStocksQuery hook
// internally and passes fetched data down to StockTable.
export type LeaderboardPageProps = Record<string, never>
