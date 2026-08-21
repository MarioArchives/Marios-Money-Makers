import { describe, expect, it } from 'vitest'
import {
  NYSE_EARLY_CLOSES,
  NYSE_HOLIDAYS,
  describeCountdown,
  formatCountdown,
  formatMarketClock,
  getMarketStatus,
  isTradingDay,
  marketLocalToInstant,
} from './marketHours'

// Fixed instants, in UTC. New York is UTC-4 (EDT) from the second Sunday of
// March to the first Sunday of November, UTC-5 (EST) otherwise; so 09:30 ET
// is 13:30Z in summer and 14:30Z in winter.
const at = (iso: string): Date => new Date(iso)
const ms = (h: number, m = 0, s = 0): number => ((h * 60 + m) * 60 + s) * 1000

describe('getMarketStatus', () => {
  it('is closed on a weekday morning before 09:30 ET, counting down to that day\'s open', () => {
    // Wednesday 19 Aug 2026, 08:00 EDT
    const status = getMarketStatus(at('2026-08-19T12:00:00Z'))
    expect(status.isOpen).toBe(false)
    expect(status.nextOpen.toISOString()).toBe('2026-08-19T13:30:00.000Z')
    expect(status.nextClose.toISOString()).toBe('2026-08-19T20:00:00.000Z')
    expect(status.isEarlyClose).toBe(false)
  })

  it('is open during the session, counting down to 16:00 ET', () => {
    // Wednesday 19 Aug 2026, 14:00 EDT
    const status = getMarketStatus(at('2026-08-19T18:00:00Z'))
    expect(status.isOpen).toBe(true)
    expect(status.nextClose.toISOString()).toBe('2026-08-19T20:00:00.000Z')
    // ...and already knows the following session's open.
    expect(status.nextOpen.toISOString()).toBe('2026-08-20T13:30:00.000Z')
  })

  it('is closed after 16:00 ET on a weekday, counting down to tomorrow\'s open', () => {
    // Wednesday 19 Aug 2026, 17:00 EDT
    const status = getMarketStatus(at('2026-08-19T21:00:00Z'))
    expect(status.isOpen).toBe(false)
    expect(status.nextOpen.toISOString()).toBe('2026-08-20T13:30:00.000Z')
    expect(status.nextClose.toISOString()).toBe('2026-08-20T20:00:00.000Z')
  })

  it('treats the open instant as open and the close instant as closed', () => {
    expect(getMarketStatus(at('2026-08-19T13:29:59.999Z')).isOpen).toBe(false)
    expect(getMarketStatus(at('2026-08-19T13:30:00.000Z')).isOpen).toBe(true)
    expect(getMarketStatus(at('2026-08-19T19:59:59.999Z')).isOpen).toBe(true)
    expect(getMarketStatus(at('2026-08-19T20:00:00.000Z')).isOpen).toBe(false)
  })

  it('skips the weekend: Friday after close counts down to Monday 09:30 ET', () => {
    // Friday 21 Aug 2026, 17:00 EDT
    const status = getMarketStatus(at('2026-08-21T21:00:00Z'))
    expect(status.isOpen).toBe(false)
    expect(status.nextOpen.toISOString()).toBe('2026-08-24T13:30:00.000Z')
  })

  it('skips the weekend: Saturday counts down to Monday 09:30 ET', () => {
    const status = getMarketStatus(at('2026-08-22T15:00:00Z'))
    expect(status.isOpen).toBe(false)
    expect(status.nextOpen.toISOString()).toBe('2026-08-24T13:30:00.000Z')
  })

  it('skips exchange holidays: Labor Day 2026-09-07 counts down to Tuesday 09-08', () => {
    // Monday 7 Sep 2026, noon EDT — a weekday, but the exchange is shut.
    const onHoliday = getMarketStatus(at('2026-09-07T16:00:00Z'))
    expect(onHoliday.isOpen).toBe(false)
    expect(onHoliday.nextOpen.toISOString()).toBe('2026-09-08T13:30:00.000Z')

    // The Friday before it jumps straight over the long weekend.
    const fridayBefore = getMarketStatus(at('2026-09-04T21:00:00Z'))
    expect(fridayBefore.nextOpen.toISOString()).toBe('2026-09-08T13:30:00.000Z')
  })

  it('chains a holiday into a weekend: New Year\'s Eve 2026 counts down to Monday 4 Jan 2027', () => {
    // Thursday 31 Dec 2026, 17:00 EST; Fri 1 Jan is a holiday.
    const status = getMarketStatus(at('2026-12-31T22:00:00Z'))
    expect(status.nextOpen.toISOString()).toBe('2027-01-04T14:30:00.000Z')
  })

  it('resolves 09:30 ET correctly on both sides of DST', () => {
    // Tuesday 10 Mar 2026 (EDT, clocks went forward 8 Mar), 08:00 local.
    const march = getMarketStatus(at('2026-03-10T12:00:00Z'))
    expect(march.nextOpen.toISOString()).toBe('2026-03-10T13:30:00.000Z')
    expect(march.nextClose.toISOString()).toBe('2026-03-10T20:00:00.000Z')

    // Tuesday 10 Nov 2026 (EST, clocks went back 1 Nov), 08:00 local.
    const november = getMarketStatus(at('2026-11-10T13:00:00Z'))
    expect(november.nextOpen.toISOString()).toBe('2026-11-10T14:30:00.000Z')
    expect(november.nextClose.toISOString()).toBe('2026-11-10T21:00:00.000Z')
  })

  it('handles the 13:00 ET early close on the day after Thanksgiving', () => {
    // Friday 27 Nov 2026, 12:00 EST — still open, but only for an hour.
    const open = getMarketStatus(at('2026-11-27T17:00:00Z'))
    expect(open.isOpen).toBe(true)
    expect(open.isEarlyClose).toBe(true)
    expect(open.nextClose.toISOString()).toBe('2026-11-27T18:00:00.000Z')

    // 14:00 EST the same day: closed, next open is Monday.
    const closed = getMarketStatus(at('2026-11-27T19:00:00Z'))
    expect(closed.isOpen).toBe(false)
    expect(closed.isEarlyClose).toBe(false)
    expect(closed.nextOpen.toISOString()).toBe('2026-11-30T14:30:00.000Z')
  })
})

describe('isTradingDay / marketLocalToInstant', () => {
  it('knows weekends and listed holidays are not trading days', () => {
    expect(isTradingDay({ year: 2026, month: 8, day: 19 })).toBe(true) // Wed
    expect(isTradingDay({ year: 2026, month: 8, day: 22 })).toBe(false) // Sat
    expect(isTradingDay({ year: 2026, month: 8, day: 23 })).toBe(false) // Sun
    expect(isTradingDay({ year: 2026, month: 12, day: 25 })).toBe(false) // Christmas
    expect(isTradingDay({ year: 2027, month: 7, day: 5 })).toBe(false) // Independence Day (obs.)
  })

  it('lists the verified 2026 and 2027 NYSE calendars', () => {
    expect([...NYSE_HOLIDAYS].filter((d) => d.startsWith('2026-'))).toHaveLength(10)
    expect([...NYSE_HOLIDAYS].filter((d) => d.startsWith('2027-'))).toHaveLength(10)
    expect(NYSE_EARLY_CLOSES.has('2026-12-24')).toBe(true)
  })

  it('converts a New York wall time to the right instant in summer and winter', () => {
    expect(marketLocalToInstant({ year: 2026, month: 7, day: 1 }, { hour: 9, minute: 30 }).toISOString()).toBe(
      '2026-07-01T13:30:00.000Z',
    )
    expect(marketLocalToInstant({ year: 2026, month: 1, day: 5 }, { hour: 16, minute: 0 }).toISOString()).toBe(
      '2026-01-05T21:00:00.000Z',
    )
  })

  it('is exact on DST-change days themselves', () => {
    // 8 Mar 2026: clocks forward at 02:00; 09:30 that day is already EDT.
    expect(marketLocalToInstant({ year: 2026, month: 3, day: 8 }, { hour: 9, minute: 30 }).toISOString()).toBe(
      '2026-03-08T13:30:00.000Z',
    )
    // 1 Nov 2026: clocks back at 02:00; 16:00 that day is EST.
    expect(marketLocalToInstant({ year: 2026, month: 11, day: 1 }, { hour: 16, minute: 0 }).toISOString()).toBe(
      '2026-11-01T21:00:00.000Z',
    )
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
