export interface ErrorBadgeProps {
  /** True when the backend served a cached/last-known value for this stock. */
  isStale: boolean
  /**
   * The backend's per-stock error, if any. Used only as a second signal
   * that the entry is degraded — the string itself is never rendered.
   */
  error?: string | null
}
