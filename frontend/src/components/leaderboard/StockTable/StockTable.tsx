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

/** Renders one `StockRow` per `stocks` entry, in the order given. Re-ranking
 * animates via a FLIP pass (see ARCHITECTURE.md); `isDegraded` greys the
 * board when the query is failing. */
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

      // Snap back without animating, force a reflow to commit it, then
      // release the transition so the stylesheet carries the row home.
      slot.style.transition = 'none'
      slot.style.transform = `translateY(${dy}px)`
      void slot.offsetHeight
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
    // Keyed on `stocks` only: re-measuring on other re-renders would read
    // mid-flight transformed positions as movement, undoing the slide.
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
