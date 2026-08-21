import { useLayoutEffect, useRef, useState } from 'react'
import type { StockTableProps } from './StockTable.props'
import { StockRow } from '../StockRow/StockRow'
import { computeFlipOffsets, computeRankDeltas, rankOf } from '../../../utils/flip'
import './StockTable.css'

/** How long a re-ranked row keeps its up/down emphasis after sliding (ms); matches the CSS. */
export const MOVED_EMPHASIS_MS = 900
/** How long the ▲n/▼n rank-delta chip stays mounted (ms); matches `--motion-rank-delta`. */
export const RANK_DELTA_MS = 2200
/** Per-row slide stagger, by new rank, so a re-sort ripples rather than snaps. */
const STAGGER_MS = 16
const STAGGER_MAX_MS = 96

const NO_DELTAS: ReadonlyMap<string, number> = new Map()

/**
 * Renders the full leaderboard: one `StockRow` per entry in `stocks`, in
 * the order given (the caller/query layer owns sorting — see
 * `utils/sortStocks.ts`; the page passes the board sorted by today's
 * change, highest first).
 *
 * Reordering is animated with a FLIP pass so every row visibly slides
 * from its old rank to its new one on each refresh:
 *
 * - First/Last: after every commit, each row slot's top is measured
 *   *relative to the rows container* (never the viewport — scrolling or
 *   resizing between two polls must not read as movement) and compared
 *   with the previous commit's measurement (`computeFlipOffsets`).
 * - Invert: a slot that moved is snapped back to its old position with
 *   transitions off, and the style is flushed.
 * - Play: the inline transform is cleared, so the stylesheet transition
 *   (`--motion-reorder`, slightly springy) carries it to its new place,
 *   with a small stagger by new rank. Rows that kept their rank get no
 *   transform at all; new/removed rows just appear/disappear.
 * - A poll landing mid-slide is fine: the measurement sees the row's
 *   current *visual* position, so it continues from there. (For the same
 *   reason the pass only runs when `stocks` changes — never on a re-render
 *   of an unchanged board, which would re-read rows still in flight.)
 *
 * Direction is made legible: slots that rose get `is-moved is-moved-up`
 * (green tint, slight lift, top of the stack), slots that fell get
 * `is-moved is-moved-down` (red tint, sits slightly back, still above
 * static rows) for `MOVED_EMPHASIS_MS`, and their `StockRow` shows a
 * `▲n`/`▼n` rank-delta chip for `RANK_DELTA_MS`. Rank deltas are state
 * (they change what the row renders); the motion classes are applied
 * straight to the DOM alongside the FLIP transform so no extra render is
 * needed for them. `prefers-reduced-motion` drops the slide/lift via CSS.
 *
 * `isDegraded` greys the whole board at once, for when the leaderboard
 * query itself is failing and every row on screen is a last known figure.
 */
export function StockTable({ stocks, isDegraded = false }: StockTableProps): JSX.Element {
  const rowsRef = useRef<HTMLDivElement>(null)
  const slotRefs = useRef(new Map<string, HTMLDivElement>())
  const previousTops = useRef(new Map<string, number>())
  const previousRanks = useRef<Map<string, number> | null>(null)
  const emphasisTimers = useRef(new Map<string, number>())
  const rankDeltaTimer = useRef<number | undefined>(undefined)
  const [rankDeltas, setRankDeltas] = useState(NO_DELTAS)

  useLayoutEffect(() => {
    const container = rowsRef.current
    if (!container) {
      return
    }

    // --- Rank deltas (order-based, independent of layout) -----------------
    const nextRanks = rankOf(stocks.map((stock) => stock.ticker))
    if (previousRanks.current) {
      const deltas = computeRankDeltas(previousRanks.current, nextRanks)
      if (deltas.size > 0) {
        // A fresh re-rank replaces any chips still showing from the last one.
        setRankDeltas(deltas)
        window.clearTimeout(rankDeltaTimer.current)
        rankDeltaTimer.current = window.setTimeout(() => {
          rankDeltaTimer.current = undefined
          setRankDeltas(NO_DELTAS)
        }, RANK_DELTA_MS)
      }
    }
    previousRanks.current = nextRanks

    // --- FLIP (position-based) -------------------------------------------
    const containerTop = container.getBoundingClientRect().top
    const nextTops = new Map<string, number>()
    slotRefs.current.forEach((slot, ticker) => {
      nextTops.set(ticker, slot.getBoundingClientRect().top - containerTop)
    })

    const offsets = computeFlipOffsets(previousTops.current, nextTops)
    offsets.forEach((dy, ticker) => {
      const slot = slotRefs.current.get(ticker)
      if (!slot) {
        return
      }
      // dy > 0 means the row's old spot was lower on the page: it rose.
      const direction = dy > 0 ? 'is-moved-up' : 'is-moved-down'
      const rank = nextRanks.get(ticker) ?? 0

      // Invert: put the row back where it was, without animating...
      slot.style.transition = 'none'
      slot.style.transform = `translateY(${dy}px)`
      // ...flush that style so the browser commits it...
      void slot.offsetHeight
      // ...then release it: the stylesheet transition carries it home,
      // each row a beat after the one ranked above it.
      slot.style.transition = ''
      slot.style.transform = ''
      slot.style.transitionDelay = `${Math.min(rank * STAGGER_MS, STAGGER_MAX_MS)}ms`

      slot.classList.remove('is-moved-up', 'is-moved-down')
      slot.classList.add('is-moved', direction)
      const pending = emphasisTimers.current.get(ticker)
      if (pending !== undefined) {
        window.clearTimeout(pending)
      }
      emphasisTimers.current.set(
        ticker,
        window.setTimeout(() => {
          slot.classList.remove('is-moved', 'is-moved-up', 'is-moved-down')
          slot.style.transitionDelay = ''
          emphasisTimers.current.delete(ticker)
        }, MOVED_EMPHASIS_MS),
      )
    })

    previousTops.current = nextTops
    // Keyed on `stocks` on purpose: the rank-delta state update above (and
    // any other re-render with the same board) must not re-measure, because
    // mid-flight slots report their *transformed* position and a second
    // pass would read the slide itself as a move back the other way.
  }, [stocks])

  useLayoutEffect(() => {
    const timers = emphasisTimers.current
    return () => {
      timers.forEach((id) => window.clearTimeout(id))
      timers.clear()
      window.clearTimeout(rankDeltaTimer.current)
    }
  }, [])

  return (
    <div
      className={`stock-table${isDegraded ? ' stock-table--degraded' : ''}`}
      data-testid="stock-table"
    >
      <div className="stock-table__head" aria-hidden="true">
        <span className="eyebrow">Company</span>
        <span className="eyebrow stock-table__head-price">Price</span>
        <span className="eyebrow stock-table__head-stats">
          Today <span className="stock-table__sort-mark">▼</span>
        </span>
      </div>
      <div className="stock-table__rows" ref={rowsRef}>
        {stocks.map((stock) => (
          <div
            key={stock.ticker}
            className="stock-table__slot"
            data-testid="stock-slot"
            data-ticker={stock.ticker}
            ref={(el) => {
              if (el) {
                slotRefs.current.set(stock.ticker, el)
              } else {
                slotRefs.current.delete(stock.ticker)
              }
            }}
          >
            <StockRow stock={stock} rankDelta={rankDeltas.get(stock.ticker)} />
          </div>
        ))}
      </div>
    </div>
  )
}
