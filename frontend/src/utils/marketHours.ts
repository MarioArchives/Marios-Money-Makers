/**
 * NYSE / Nasdaq regular-session clock, used by `MarketStatusBanner` to
 * explain why the board stops moving outside US hours.
 *
 * Regular session: 09:30-16:00 America/New_York, Monday-Friday, minus the
 * exchange holidays below; a handful of days close early at 13:00. All
 * wall-clock arithmetic goes through `Intl.DateTimeFormat` with the
 * `America/New_York` zone, so DST is handled by the platform's tz data and
 * no date library is needed. Everything here is pure (takes `now`, returns
 * values) so it can be unit-tested with fixed instants.
 */

export const MARKET_TIME_ZONE = 'America/New_York'

export interface ClockTime {
  hour: number
  minute: number
}

export const MARKET_OPEN: ClockTime = { hour: 9, minute: 30 }
export const MARKET_CLOSE: ClockTime = { hour: 16, minute: 0 }
export const MARKET_EARLY_CLOSE: ClockTime = { hour: 13, minute: 0 }

/**
 * NYSE full-day closures, as `YYYY-MM-DD` in New York local dates. Source:
 * NYSE Group holiday & early-closings press releases (2026/2027/2028
 * calendar, Nov 2025). Extend this list each year — dates after the last
 * entry are treated as ordinary weekdays, so an un-listed holiday will
 * merely show a countdown to a session that never comes.
 */
export const NYSE_HOLIDAYS: ReadonlySet<string> = new Set([
  // 2026
  '2026-01-01', // New Year's Day
  '2026-01-19', // Martin Luther King, Jr. Day
  '2026-02-16', // Washington's Birthday
  '2026-04-03', // Good Friday
  '2026-05-25', // Memorial Day
  '2026-06-19', // Juneteenth
  '2026-07-03', // Independence Day (observed)
  '2026-09-07', // Labor Day
  '2026-11-26', // Thanksgiving Day
  '2026-12-25', // Christmas Day
  // 2027
  '2027-01-01', // New Year's Day
  '2027-01-18', // Martin Luther King, Jr. Day
  '2027-02-15', // Washington's Birthday
  '2027-03-26', // Good Friday
  '2027-05-31', // Memorial Day
  '2027-06-18', // Juneteenth (observed)
  '2027-07-05', // Independence Day (observed)
  '2027-09-06', // Labor Day
  '2027-11-25', // Thanksgiving Day
  '2027-12-24', // Christmas Day (observed)
])

/** Days the regular session ends at 13:00 ET (same source as above). */
export const NYSE_EARLY_CLOSES: ReadonlySet<string> = new Set([
  '2026-11-27', // day after Thanksgiving
  '2026-12-24', // Christmas Eve
  '2027-11-26', // day after Thanksgiving
])

export interface MarketStatus {
  /** True during a regular session (open <= now < close). */
  isOpen: boolean
  /** Start of the next session (or the current one's successor while open). */
  nextOpen: Date
  /** End of the current session while open, otherwise the end of the next one. */
  nextClose: Date
  /** Whether the session `nextClose` belongs to is a 13:00 ET early close. */
  isEarlyClose: boolean
}

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

const MS_PER_DAY = 86_400_000

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

function isoDate({ year, month, day }: LocalDate): string {
  const mm = String(month).padStart(2, '0')
  const dd = String(day).padStart(2, '0')
  return `${year}-${mm}-${dd}`
}

/** Calendar weekday (0 = Sunday) of a local date — zone-independent. */
function weekdayOf({ year, month, day }: LocalDate): number {
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay()
}

function addDays({ year, month, day }: LocalDate, days: number): LocalDate {
  const d = new Date(Date.UTC(year, month - 1, day) + days * MS_PER_DAY)
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() }
}

/** Weekday and not an exchange holiday. */
export function isTradingDay(date: LocalDate): boolean {
  const weekday = weekdayOf(date)
  return weekday !== 0 && weekday !== 6 && !NYSE_HOLIDAYS.has(isoDate(date))
}

/**
 * The instant at which New York's wall clock reads `date` + `time`.
 *
 * Start from the same wall time read as UTC, measure how far the New York
 * clock is from that guess, and correct; a second pass covers a guess that
 * landed on the other side of a DST change (which happens at 02:00, hours
 * away from either session boundary, so one pass is normally exact).
 */
export function marketLocalToInstant(date: LocalDate, time: ClockTime): Date {
  const target = Date.UTC(date.year, date.month - 1, date.day, time.hour, time.minute, 0, 0)
  let guess = target
  for (let pass = 0; pass < 2; pass += 1) {
    const local = toMarketLocal(new Date(guess))
    const read = Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second, 0)
    if (read === target) {
      break
    }
    guess += target - read
  }
  return new Date(guess)
}

interface Session {
  open: Date
  close: Date
  isEarlyClose: boolean
}

function sessionOn(date: LocalDate): Session {
  const isEarlyClose = NYSE_EARLY_CLOSES.has(isoDate(date))
  return {
    open: marketLocalToInstant(date, MARKET_OPEN),
    close: marketLocalToInstant(date, isEarlyClose ? MARKET_EARLY_CLOSE : MARKET_CLOSE),
    isEarlyClose,
  }
}

/** First trading day strictly after `date` (bounded: no week is all holidays). */
function nextTradingDayAfter(date: LocalDate): LocalDate {
  let candidate = addDays(date, 1)
  for (let i = 0; i < 14 && !isTradingDay(candidate); i += 1) {
    candidate = addDays(candidate, 1)
  }
  return candidate
}

/**
 * Where the market is relative to `now`: open or closed, and the instants
 * of the next open and next close.
 */
export function getMarketStatus(now: Date): MarketStatus {
  const today = toMarketLocal(now)
  const t = now.getTime()

  if (isTradingDay(today)) {
    const session = sessionOn(today)
    if (t < session.open.getTime()) {
      return { isOpen: false, nextOpen: session.open, nextClose: session.close, isEarlyClose: session.isEarlyClose }
    }
    if (t < session.close.getTime()) {
      const following = sessionOn(nextTradingDayAfter(today))
      return { isOpen: true, nextOpen: following.open, nextClose: session.close, isEarlyClose: session.isEarlyClose }
    }
  }

  const next = sessionOn(nextTradingDayAfter(today))
  return { isOpen: false, nextOpen: next.open, nextClose: next.close, isEarlyClose: next.isEarlyClose }
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
