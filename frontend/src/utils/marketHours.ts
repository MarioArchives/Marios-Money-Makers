/**
 * Formatting helpers for the NYSE / Nasdaq market clock shown by
 * `MarketStatusBanner`.
 *
 * The clock itself — whether the market is open, and the instants of the
 * next open/close — comes from the backend (`GET /api/market/clock`,
 * sourced from Alpaca's `/v2/clock`); this module keeps no session times or
 * holiday calendar of its own. It only turns instants and durations into
 * text: a New York wall-clock reading, a viewer-local reading, and the two
 * countdown formats. All wall-clock arithmetic goes through
 * `Intl.DateTimeFormat` with the `America/New_York` zone, so DST is handled
 * by the platform's tz data and no date library is needed. Everything here
 * is pure (takes an instant, returns a value) so it can be unit-tested with
 * fixed instants.
 */

export const MARKET_TIME_ZONE = 'America/New_York'

/** The regular session's close, New York wall-clock time — the definition of "early" below. */
const REGULAR_CLOSE = { hour: 16, minute: 0 }

/** A calendar date in the market's local zone. `month` is 1-12. */
interface LocalDate {
  year: number
  month: number
  day: number
}

interface LocalDateTime extends LocalDate {
  hour: number
  minute: number
  second: number
}

// Built once: constructing Intl formatters is expensive and this runs every
// second. `hourCycle: 'h23'` avoids the "24:00" quirk of `hour12: false`.
const etFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TIME_ZONE,
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

/** Wall-clock time in New York for the given instant. */
export function toMarketLocal(instant: Date): LocalDateTime {
  const parts: Record<string, number> = {}
  for (const part of etFormatter.formatToParts(instant)) {
    if (part.type !== 'literal') {
      parts[part.type] = Number(part.value)
    }
  }
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour,
    minute: parts.minute,
    second: parts.second,
  }
}

const pad2 = (n: number): string => String(n).padStart(2, '0')

/**
 * A remaining duration for the countdown: `"4h 17m 03s"` inside a day,
 * `"2d 4h 17m"` beyond it (seconds would just be noise at that range), and
 * `"17m 03s"` under an hour. Rounds up to whole seconds so the display
 * reaches `0m 00s` exactly as the boundary passes, never a second early;
 * negative input clamps to zero.
 */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const days = Math.floor(total / 86_400)
  const hours = Math.floor((total % 86_400) / 3_600)
  const minutes = Math.floor((total % 3_600) / 60)
  const seconds = total % 60

  if (days > 0) {
    return `${days}d ${hours}h ${pad2(minutes)}m`
  }
  if (hours > 0) {
    return `${hours}h ${pad2(minutes)}m ${pad2(seconds)}s`
  }
  return `${minutes}m ${pad2(seconds)}s`
}

/**
 * Coarser, spoken form for an accessible label — no seconds, so the text
 * only changes once a minute: `"4 hours 17 minutes"`, `"2 days 4 hours"`,
 * `"under a minute"`.
 */
export function describeCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const days = Math.floor(total / 86_400)
  const hours = Math.floor((total % 86_400) / 3_600)
  const minutes = Math.floor((total % 3_600) / 60)
  const plural = (n: number, unit: string): string => `${n} ${unit}${n === 1 ? '' : 's'}`

  if (days > 0) {
    return `${plural(days, 'day')} ${plural(hours, 'hour')}`
  }
  if (hours > 0) {
    return `${plural(hours, 'hour')} ${plural(minutes, 'minute')}`
  }
  if (minutes > 0) {
    return plural(minutes, 'minute')
  }
  return 'under a minute'
}

/** `"09:30"`-style ET clock reading of an instant, for the banner's hint. */
export function formatMarketClock(instant: Date): string {
  const local = toMarketLocal(instant)
  return `${pad2(local.hour)}:${pad2(local.minute)}`
}

// Pinned to en-GB for deterministic output (the app quotes in GBP); the zone
// is the viewer's, so this is the "in your time" reading of an ET instant.
const viewerFormatter = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZoneName: 'short',
})

/** The viewer's local reading of an instant, e.g. `"Mon 14:30 BST"`. */
export function formatViewerClock(instant: Date): string {
  return viewerFormatter.format(instant).replace(',', '')
}

/**
 * True iff `nextClose`'s New York wall-clock time lands before the regular
 * 16:00 close — i.e. the session it belongs to is a holiday early close.
 */
export function isEarlyClose(nextClose: Date): boolean {
  const local = toMarketLocal(nextClose)
  return local.hour < REGULAR_CLOSE.hour ||
    (local.hour === REGULAR_CLOSE.hour && local.minute < REGULAR_CLOSE.minute)
}
