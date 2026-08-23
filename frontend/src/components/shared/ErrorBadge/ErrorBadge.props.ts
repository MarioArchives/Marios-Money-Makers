export interface ErrorBadgeProps {
  /** True when the backend served a cached/last-known value for this stock. */
  isStale: boolean
  /** Per-stock error, if any — used only as a degraded signal, never rendered. */
  error?: string | null
}
