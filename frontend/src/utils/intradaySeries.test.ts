import { describe, expect, it } from 'vitest'
import type { HistoryPoint } from '../api/types'
import {
  GAP_BLOCK_FRACTION,
  GAP_BLOCK_MIN_SLOTS,
  MARKET_CLOSED_LABEL,
  MIN_HOLE_MS,
  buildIntradaySeries,
  isGapKey,
} from './intradaySeries'

// All instants are UTC; New York is EDT (UTC-4) in August, so the regular
// session 09:30-16:00 ET is 13:30Z-20:00Z. The builder knows nothing about
// sessions -- a block marks every hole >= MIN_HOLE_MS between bars.
const point = (t: string, close: number): HistoryPoint => ({ t, close })
const iso = (date: string, hm: string): string => `2026-08-${date}T${hm}:00Z`

const WED_1500 = iso('19', '19:00')
const WED_1515 = iso('19', '19:15')
const WED_1545 = iso('19', '19:45')
const WED_1600 = iso('19', '20:00') // after-hours bar stamped at the close
const WED_1645 = iso('19', '20:45')
const WED_1730 = iso('19', '21:30')
const THU_0930 = iso('20', '13:30')
const THU_0945 = iso('20', '13:45')
const FRI_0930 = iso('21', '13:30')

/** Expected number of placeholder columns for `n` real points. */
const slotsFor = (n: number): number =>
  Math.max(GAP_BLOCK_MIN_SLOTS, Math.round(n * GAP_BLOCK_FRACTION))

/** A minute-cadence run of bars from `startIso` (inclusive), `count` long. */
function minuteRun(startIso: string, count: number, close = 100): HistoryPoint[] {
  const start = Date.parse(startIso)
  return Array.from({ length: count }, (_, i) => ({
    t: new Date(start + i * 60_000).toISOString().replace('.000Z', 'Z'),
    close: close + i * 0.01,
  }))
}

describe('buildIntradaySeries', () => {
  it('returns an empty series for no points', () => {
    expect(buildIntradaySeries([])).toEqual({ data: [], blocks: [] })
  })

  it('passes points through unchanged (as {t, close}) when nothing crosses closed time', () => {
    const out = buildIntradaySeries([point(WED_1500, 100), point(WED_1515, 101)])
    expect(out.data).toEqual([
      { t: WED_1500, close: 100 },
      { t: WED_1515, close: 101 },
    ])
    expect(out.blocks).toEqual([])
  })

  it('drops a point whose timestamp does not parse without poisoning the span', () => {
    const out = buildIntradaySeries([point(WED_1500, 100), point('not-a-date', 5), point(WED_1515, 101)])
    expect(out.data.map((p) => p.t)).toEqual([WED_1500, WED_1515])
    expect(out.blocks).toEqual([])
  })

  it('inserts one fixed-width block of null placeholder columns across the overnight hole', () => {
    const points = [
      point(WED_1545, 100),
      point(WED_1600, 101),
      point(THU_0930, 102),
      point(THU_0945, 103),
    ]
    const out = buildIntradaySeries(points)

    const slots = slotsFor(points.length)
    expect(slots).toBe(GAP_BLOCK_MIN_SLOTS) // 4 points × 8% rounds below the minimum
    const gaps = out.data.filter((p) => isGapKey(p.t))
    expect(gaps).toHaveLength(slots)
    expect(gaps.every((p) => p.close === null)).toBe(true)
    // Unique keys, so recharts treats each as its own category.
    expect(new Set(gaps.map((p) => p.t)).size).toBe(slots)

    // The block sits between the two bars that bracket the hole; real
    // points are untouched and in order.
    expect(out.data.map((p) => (isGapKey(p.t) ? null : p.close))).toEqual([
      100,
      101,
      ...Array<null>(slots).fill(null),
      102,
      103,
    ])
    expect(out.blocks).toEqual([{ x1: gaps[0].t, x2: gaps[slots - 1].t }])
  })

  it('scales the block with the series length (8% of the real points)', () => {
    const day = minuteRun(iso('19', '13:30'), 390) // a full regular session
    const points = [...day, point(THU_0930, 100), point(THU_0945, 101)]
    const out = buildIntradaySeries(points)

    const slots = slotsFor(points.length)
    expect(slots).toBe(Math.round(392 * 0.08))
    expect(out.data.filter((p) => isGapKey(p.t))).toHaveLength(slots)
    expect(out.blocks).toHaveLength(1)
  })

  it('gives every hole of at least MIN_HOLE_MS its own block, in order', () => {
    // No session calendar on the client any more: sparse after-hours bars
    // 45 minutes apart are real holes and each gets a block, as does the
    // overnight one.
    const points = [
      point(WED_1545, 100),
      point(WED_1600, 101),
      point(WED_1645, 101.2),
      point(WED_1730, 101.1),
      point(THU_0930, 102),
      point(THU_0945, 103),
    ]
    const out = buildIntradaySeries(points)

    const slots = slotsFor(points.length)
    expect(out.blocks).toHaveLength(3)
    expect(out.data.map((p) => (isGapKey(p.t) ? null : p.close))).toEqual([
      100,
      101,
      ...Array<null>(slots).fill(null),
      101.2,
      ...Array<null>(slots).fill(null),
      101.1,
      ...Array<null>(slots).fill(null),
      102,
      103,
    ])
    const keys = out.data.map((p) => p.t)
    const firsts = out.blocks.map((b) => keys.indexOf(b.x1))
    expect(firsts).toEqual([...firsts].sort((a, b) => a - b))
    // Each block spans exactly its own placeholder keys.
    for (const block of out.blocks) {
      expect(keys.indexOf(block.x2) - keys.indexOf(block.x1)).toBe(slots - 1)
    }
  })

  it('adds no block when bars run continuously through the closed period', () => {
    // Mock-style 24/7 minute bars: the market is "closed" overnight, but
    // there is no hole in the data to mark.
    const points = minuteRun(iso('19', '19:30'), 18 * 60) // 15:30 ET Wed -> 09:30 ET Thu
    const out = buildIntradaySeries(points)

    expect(out.blocks).toEqual([])
    expect(out.data).toHaveLength(points.length)
    expect(out.data.some((p) => isGapKey(p.t))).toBe(false)
  })

  it('marks a hole of at least MIN_HOLE_MS inside the session too (the client knows no session times)', () => {
    // Minute bars 10:00-10:29 ET, silence until 13:00 ET, minute bars
    // again: a 2.5h in-session outage is a hole in the data like any
    // other -- with no calendar the client cannot tell it apart.
    const runA = minuteRun(iso('19', '14:00'), 30)
    const runB = minuteRun(iso('19', '17:00'), 30, 200)
    const points = [...runA, ...runB]
    const out = buildIntradaySeries(points)

    expect(out.blocks).toHaveLength(1)
    const slots = slotsFor(points.length)
    const closes = out.data.map((p) => (isGapKey(p.t) ? null : p.close))
    expect(closes).toEqual([
      ...runA.map((p) => p.close),
      ...Array<null>(slots).fill(null),
      ...runB.map((p) => p.close),
    ])
  })

  it('adds no block for a hole shorter than MIN_HOLE_MS even inside closed time', () => {
    const points = [
      point(iso('19', '19:40'), 100), // 15:40 ET
      point(WED_1600, 101),
      point(iso('19', '20:20'), 101.1), // 20 min after the close
      point(iso('19', '20:40'), 101.2),
    ]
    const out = buildIntradaySeries(points)

    expect(out.blocks).toEqual([])
    expect(out.data).toEqual(points)
  })

  it('treats a hole of exactly MIN_HOLE_MS as a hole, and one millisecond less as cadence', () => {
    const start = Date.parse(WED_1500)
    const atMs = (offset: number): string => new Date(start + offset).toISOString()
    const exact = buildIntradaySeries([point(WED_1500, 100), point(atMs(MIN_HOLE_MS), 101)])
    const under = buildIntradaySeries([point(WED_1500, 100), point(atMs(MIN_HOLE_MS - 1), 101)])

    expect(exact.blocks).toHaveLength(1)
    expect(under.blocks).toEqual([])
  })

  it('produces one block per hole, in chronological order', () => {
    const points = [
      point(WED_1545, 100),
      point(WED_1600, 101),
      point(THU_0930, 102),
      point(THU_0945, 103),
      point(FRI_0930, 104),
    ]
    const out = buildIntradaySeries(points)

    expect(out.blocks).toHaveLength(2)
    const slots = slotsFor(points.length)
    expect(out.data.map((p) => (isGapKey(p.t) ? null : p.close))).toEqual([
      100,
      101,
      ...Array<null>(slots).fill(null),
      102,
      103,
      ...Array<null>(slots).fill(null),
      104,
    ])
    const keys = out.data.map((p) => p.t)
    expect(keys.indexOf(out.blocks[0].x1)).toBeLessThan(keys.indexOf(out.blocks[1].x1))
  })

  it('recognises placeholder keys and nothing else', () => {
    const out = buildIntradaySeries([point(WED_1600, 101), point(THU_0930, 102)])
    const gap = out.data.find((p) => p.close === null)
    expect(gap).toBeDefined()
    expect(isGapKey(gap!.t)).toBe(true)
    expect(isGapKey(WED_1600)).toBe(false)
    expect(isGapKey('')).toBe(false)
  })

  it('exposes the message text used on the chart', () => {
    expect(MARKET_CLOSED_LABEL).toBe('No data — market closed in this period')
  })
})
