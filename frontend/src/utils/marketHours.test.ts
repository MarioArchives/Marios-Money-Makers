import { describe, expect, it } from 'vitest'
import * as marketHours from './marketHours'
import {
  MARKET_TIME_ZONE,
  describeCountdown,
  formatCountdown,
  formatMarketClock,
  isEarlyClose,
  toMarketLocal,
} from './marketHours'

// Fixed instants, in UTC. New York is UTC-4 (EDT) from the second Sunday of
// March to the first Sunday of November, UTC-5 (EST) otherwise; so 16:00 ET
// is 20:00Z in summer and 21:00Z in winter.
const at = (iso: string): Date => new Date(iso)
const ms = (h: number, m = 0, s = 0): number => ((h * 60 + m) * 60 + s) * 1000

describe('hardcoded NYSE calendar is gone', () => {
  // The market clock now comes from the backend (`GET /api/market/clock`,
  // sourced from Alpaca's `/v2/clock`); the client keeps no session times,
  // holiday list or status calculation of its own.
  it('no longer exports a session/holiday calendar or a client-side market status', () => {
    const exported = marketHours as Record<string, unknown>
    for (const name of [
      'getMarketStatus',
      'isTradingDay',
      'marketLocalToInstant',
      'closedIntervalsBetween',
      'NYSE_HOLIDAYS',
      'NYSE_EARLY_CLOSES',
      'MARKET_OPEN',
      'MARKET_CLOSE',
      'MARKET_EARLY_CLOSE',
    ]) {
      expect(exported[name], name).toBeUndefined()
    }
  })

  it('keeps the pure formatting helpers', () => {
    expect(MARKET_TIME_ZONE).toBe('America/New_York')
    expect(typeof toMarketLocal).toBe('function')
    expect(typeof formatCountdown).toBe('function')
    expect(typeof describeCountdown).toBe('function')
    expect(typeof formatMarketClock).toBe('function')
    expect(typeof marketHours.formatViewerClock).toBe('function')
    expect(typeof isEarlyClose).toBe('function')
  })
})

describe('isEarlyClose', () => {
  it('is true when the close lands before 16:00 New York time', () => {
    expect(isEarlyClose(at('2026-11-27T18:00:00Z'))).toBe(true) // 13:00 EST, day after Thanksgiving
    expect(isEarlyClose(at('2026-07-02T17:00:00Z'))).toBe(true) // 13:00 EDT
  })

  it('is false for the regular 16:00 close in summer and in winter', () => {
    expect(isEarlyClose(at('2026-08-19T20:00:00Z'))).toBe(false) // 16:00 EDT
    expect(isEarlyClose(at('2026-12-15T21:00:00Z'))).toBe(false) // 16:00 EST
  })
})

describe('toMarketLocal', () => {
  it('reads the New York wall clock on both sides of DST', () => {
    expect(toMarketLocal(at('2026-08-19T13:30:00Z'))).toMatchObject({ hour: 9, minute: 30 })
    expect(toMarketLocal(at('2026-12-15T14:30:00Z'))).toMatchObject({ hour: 9, minute: 30 })
  })
})

describe('formatCountdown', () => {
  it('shows hours, minutes and zero-padded seconds inside a day', () => {
    expect(formatCountdown(ms(4, 17, 3))).toBe('4h 17m 03s')
    expect(formatCountdown(ms(5))).toBe('5h 00m 00s')
    expect(formatCountdown(ms(23, 59, 59))).toBe('23h 59m 59s')
  })

  it('drops to minutes and seconds under an hour', () => {
    expect(formatCountdown(ms(0, 17, 3))).toBe('17m 03s')
    expect(formatCountdown(ms(0, 0, 9))).toBe('0m 09s')
  })

  it('switches to days and drops seconds beyond 24 hours', () => {
    expect(formatCountdown(ms(24))).toBe('1d 0h 00m')
    expect(formatCountdown(ms(52, 17, 30))).toBe('2d 4h 17m')
  })

  it('rounds up to whole seconds and clamps negatives to zero', () => {
    expect(formatCountdown(500)).toBe('0m 01s')
    expect(formatCountdown(0)).toBe('0m 00s')
    expect(formatCountdown(-5000)).toBe('0m 00s')
  })
})

describe('describeCountdown', () => {
  it('speaks the remaining time without seconds', () => {
    expect(describeCountdown(ms(4, 17, 3))).toBe('4 hours 17 minutes')
    expect(describeCountdown(ms(1, 1))).toBe('1 hour 1 minute')
    expect(describeCountdown(ms(52, 17))).toBe('2 days 4 hours')
    expect(describeCountdown(ms(0, 17, 3))).toBe('17 minutes')
    expect(describeCountdown(ms(0, 0, 30))).toBe('under a minute')
  })
})

describe('formatMarketClock', () => {
  it('reads an instant on the New York clock', () => {
    expect(formatMarketClock(at('2026-08-19T13:30:00Z'))).toBe('09:30')
    expect(formatMarketClock(at('2026-11-27T18:00:00Z'))).toBe('13:00')
  })
})
