import type { HistoryPoint } from '../api/types'

/**
 * Intraday (1d) chart series with market-closed blocks.
 *
 * The chart keeps a categorical x-axis (every bar gets the same width, so
 * trading hours keep all the space). The client no longer carries any
 * session calendar — the market clock lives in the backend
 * (`GET /api/market/clock`) and only describes *now*, not history — so the
 * rule here is purely data-driven: every hole of at least `MIN_HOLE_MS`
 * between consecutive bars gets a fixed-width block of placeholder columns,
 * greyed out with the "no data" message. Overnights, weekends and holidays
 * all show up as exactly such holes; the known tradeoff is that a long
 * in-session outage is marked the same way (with no calendar the client
 * cannot tell the two apart).
 */

/** A plotted row: a real bar, or a null-close placeholder column. */
export interface IntradayPlotPoint {
  t: string
  /** `null` on a placeholder column: the line is not drawn across it. */
  close: number | null
}

/** One greyed block: the category keys of its first and last placeholder column. */
export interface ClosedBlock {
  x1: string
  x2: string
}

export interface IntradaySeries {
  data: IntradayPlotPoint[]
  blocks: ClosedBlock[]
}

export const MARKET_CLOSED_LABEL = 'No data — market closed in this period'

/** Block width as a fraction of the number of real bars plotted. */
export const GAP_BLOCK_FRACTION = 0.08

/** A block is never narrower than this many columns. */
export const GAP_BLOCK_MIN_SLOTS = 4

/**
 * Only a hole at least this long between consecutive bars gets a block —
 * shorter gaps are the normal cadence of sparse pre-/after-hours bars, and
 * data that runs continuously through the night (the mock server) has no
 * hole to mark at all.
 */
export const MIN_HOLE_MS = 30 * 60_000

const GAP_KEY_PREFIX = 'gap:'

/** True for the `t` of a placeholder column. */
export function isGapKey(t: string): boolean {
  return t.startsWith(GAP_KEY_PREFIX)
}

interface Parsed {
  t: string
  ts: number
  close: number
}

export function buildIntradaySeries(points: HistoryPoint[]): IntradaySeries {
  const parsed: Parsed[] = points
    .map((p) => ({ t: p.t, ts: Date.parse(p.t), close: p.close }))
    .filter((p) => Number.isFinite(p.ts))

  if (parsed.length === 0) {
    return { data: [], blocks: [] }
  }

  // Every hole of at least MIN_HOLE_MS between consecutive bars gets a
  // block after the earlier bar, in order.
  const breakAfter = new Set<number>()
  for (let i = 0; i < parsed.length - 1; i += 1) {
    if (parsed[i + 1].ts - parsed[i].ts >= MIN_HOLE_MS) {
      breakAfter.add(i)
    }
  }

  const slots = Math.max(GAP_BLOCK_MIN_SLOTS, Math.round(parsed.length * GAP_BLOCK_FRACTION))
  const data: IntradayPlotPoint[] = []
  const blocks: ClosedBlock[] = []
  parsed.forEach((p, i) => {
    data.push({ t: p.t, close: p.close })
    if (breakAfter.has(i)) {
      const keys = Array.from({ length: slots }, (_, k) => `${GAP_KEY_PREFIX}${p.t}:${k}`)
      for (const key of keys) {
        data.push({ t: key, close: null })
      }
      blocks.push({ x1: keys[0], x2: keys[keys.length - 1] })
    }
  })

  return { data, blocks }
}
