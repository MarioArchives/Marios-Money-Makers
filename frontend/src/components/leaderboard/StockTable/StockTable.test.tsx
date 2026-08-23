import { useState } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, it, expect, vi } from 'vitest'
import { MOVED_EMPHASIS_MS, RANK_DELTA_MS, StockTable } from './StockTable'
import type { StockSummary } from '../../../api/types'

function makeStock(overrides: Partial<StockSummary> = {}): StockSummary {
  return {
    ticker: 'AZN.L',
    name: 'AstraZeneca',
    sector: 'Pharma',
    price: 1234.56,
    currency: 'GBP',
    previous_close: 1200,
    change: 34.56,
    change_percent: 2.88,
    is_stale: false,
    error: null,
    ...overrides,
  }
}

const STOCKS: StockSummary[] = [
  makeStock({ ticker: 'AZN.L', name: 'AstraZeneca' }),
  makeStock({ ticker: 'GSK.L', name: 'GSK' }),
  makeStock({ ticker: 'VOD.L', name: 'Vodafone' }),
]

function renderTable(stocks: StockSummary[]) {
  return render(
    <MemoryRouter>
      <StockTable stocks={stocks} />
    </MemoryRouter>,
  )
}

describe('StockTable', () => {
  it('renders one row (link to the stock detail page) per stock', () => {
    renderTable(STOCKS)

    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(STOCKS.length)
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/stock/AZN.L',
      '/stock/GSK.L',
      '/stock/VOD.L',
    ])
  })

  it("renders each stock's name", () => {
    renderTable(STOCKS)

    for (const stock of STOCKS) {
      expect(screen.getByText(stock.name)).toBeInTheDocument()
    }
  })

  it('renders no rows for an empty stock list', () => {
    renderTable([])

    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })
})

describe('StockTable reordering', () => {
  it('renders rows in the given order and moves them when the order changes', () => {
    const { rerender } = renderTable(STOCKS)
    expect(screen.getAllByTestId('stock-slot').map((el) => el.getAttribute('data-ticker'))).toEqual([
      'AZN.L',
      'GSK.L',
      'VOD.L',
    ])

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[2], STOCKS[0], STOCKS[1]]} />
      </MemoryRouter>,
    )

    expect(screen.getAllByTestId('stock-slot').map((el) => el.getAttribute('data-ticker'))).toEqual([
      'VOD.L',
      'AZN.L',
      'GSK.L',
    ])
    // Still exactly one link per stock after the move — rows are keyed by ticker, not recreated.
    expect(screen.getAllByRole('link')).toHaveLength(3)
  })
})

/** jsdom has no layout, so these tests stub getBoundingClientRect() to drive the FLIP pass. */
describe('StockTable FLIP reorder', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  /** Fake layout: rows are 100px tall, stacked inside a container at `scrollTop`. */
  function stubLayout(scrollTop: () => number): void {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const containerTop = -scrollTop()
      if (this.classList.contains('stock-table__rows')) {
        return { top: containerTop } as DOMRect
      }
      if (this.classList.contains('stock-table__slot')) {
        const index = Array.from(this.parentElement?.children ?? []).indexOf(this)
        return { top: containerTop + index * 100 } as DOMRect
      }
      return { top: 0 } as DOMRect
    })
  }

  it('marks rows that changed rank as moved and clears the mark after the emphasis window', () => {
    vi.useFakeTimers()
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[2], STOCKS[0], STOCKS[1]]} />
      </MemoryRouter>,
    )

    const moved = screen.getAllByTestId('stock-slot').filter((el) => el.classList.contains('is-moved'))
    expect(moved.map((el) => el.getAttribute('data-ticker')).sort()).toEqual(['AZN.L', 'GSK.L', 'VOD.L'])

    act(() => {
      vi.advanceTimersByTime(MOVED_EMPHASIS_MS + 1)
    })
    expect(screen.getAllByTestId('stock-slot').some((el) => el.classList.contains('is-moved'))).toBe(false)
  })

  it('tells risers from fallers: is-moved-up for rows that climbed, is-moved-down for rows that fell', () => {
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    // VOD climbs two places; AZN and GSK each slip one.
    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[2], STOCKS[0], STOCKS[1]]} />
      </MemoryRouter>,
    )

    const byTicker = Object.fromEntries(
      screen.getAllByTestId('stock-slot').map((el) => [el.getAttribute('data-ticker'), el.className]),
    )
    expect(byTicker['VOD.L']).toContain('is-moved-up')
    expect(byTicker['VOD.L']).not.toContain('is-moved-down')
    expect(byTicker['AZN.L']).toContain('is-moved-down')
    expect(byTicker['GSK.L']).toContain('is-moved-down')
    expect(byTicker['AZN.L']).not.toContain('is-moved-up')
  })

  it('clears the direction classes with the emphasis and keeps the inline transform released', () => {
    vi.useFakeTimers()
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[1], STOCKS[0], STOCKS[2]]} />
      </MemoryRouter>,
    )

    const slots = () => screen.getAllByTestId('stock-slot')
    // The FLIP "play" step has released the transform so the stylesheet
    // transition carries the row; only the stagger delay is left inline.
    for (const slot of slots().filter((el) => el.classList.contains('is-moved'))) {
      expect(slot.style.transform).toBe('')
      expect(slot.style.transition).toBe('')
      expect(slot.style.transitionDelay).toMatch(/^\d+ms$/)
    }

    act(() => {
      vi.advanceTimersByTime(MOVED_EMPHASIS_MS + 1)
    })
    for (const slot of slots()) {
      expect(slot.classList.contains('is-moved-up')).toBe(false)
      expect(slot.classList.contains('is-moved-down')).toBe(false)
      expect(slot.style.transitionDelay).toBe('')
    }
  })

  it('does not re-measure on a re-render of the same board (rows in flight would read as a move back)', () => {
    // Mimic mid-slide: after the reorder, rects still report pre-reorder
    // layout, since a slot's rect includes its current transform.
    let inFlight = false
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('stock-table__rows')) {
        return { top: 0 } as DOMRect
      }
      if (this.classList.contains('stock-table__slot')) {
        const ticker = this.getAttribute('data-ticker') ?? ''
        const order = inFlight ? ['AZN.L', 'GSK.L', 'VOD.L'] : Array.from(this.parentElement?.children ?? []).map((c) => c.getAttribute('data-ticker'))
        return { top: order.indexOf(ticker) * 100 } as DOMRect
      }
      return { top: 0 } as DOMRect
    })

    function Harness({ stocks }: { stocks: StockSummary[] }) {
      const [, setTick] = useState(0)
      return (
        <MemoryRouter>
          <button type="button" onClick={() => setTick((t) => t + 1)}>rerender</button>
          <StockTable stocks={stocks} />
        </MemoryRouter>
      )
    }
    const reordered = [STOCKS[2], STOCKS[0], STOCKS[1]]
    const { rerender } = render(<Harness stocks={STOCKS} />)
    rerender(<Harness stocks={reordered} />)

    const classes = () =>
      Object.fromEntries(screen.getAllByTestId('stock-slot').map((el) => [el.getAttribute('data-ticker'), el.className]))
    expect(classes()['VOD.L']).toContain('is-moved-up')
    expect(classes()['AZN.L']).toContain('is-moved-down')

    inFlight = true
    fireEvent.click(screen.getByRole('button', { name: 'rerender' })) // same `stocks` reference

    expect(classes()['VOD.L']).toContain('is-moved-up')
    expect(classes()['VOD.L']).not.toContain('is-moved-down')
    expect(classes()['AZN.L']).toContain('is-moved-down')
    expect(classes()['AZN.L']).not.toContain('is-moved-up')
  })

  it('staggers the slide by new rank, top row first', () => {
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[2], STOCKS[0], STOCKS[1]]} />
      </MemoryRouter>,
    )

    const delays = screen
      .getAllByTestId('stock-slot')
      .map((el) => parseInt(el.style.transitionDelay, 10))
    expect(delays[0]).toBe(0)
    expect(delays[1]).toBeGreaterThan(delays[0])
    expect(delays[2]).toBeGreaterThan(delays[1])
  })

  it('flips the direction on a reverse move instead of accumulating classes', () => {
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[1], STOCKS[0], STOCKS[2]]} />
      </MemoryRouter>,
    )
    // Move them back: the direction must flip, not accumulate.
    rerender(
      <MemoryRouter>
        <StockTable stocks={[...STOCKS]} />
      </MemoryRouter>,
    )

    const byTicker = Object.fromEntries(
      screen.getAllByTestId('stock-slot').map((el) => [el.getAttribute('data-ticker'), el.classList]),
    )
    expect(byTicker['AZN.L'].contains('is-moved-up')).toBe(true)
    expect(byTicker['AZN.L'].contains('is-moved-down')).toBe(false)
    expect(byTicker['GSK.L'].contains('is-moved-down')).toBe(true)
    expect(byTicker['GSK.L'].contains('is-moved-up')).toBe(false)
  })

  it('does not treat an unchanged order as movement', () => {
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[...STOCKS]} />
      </MemoryRouter>,
    )

    expect(screen.getAllByTestId('stock-slot').some((el) => el.classList.contains('is-moved'))).toBe(false)
  })

  it('ignores page scroll between two refreshes (measures relative to the container)', () => {
    let scroll = 0
    stubLayout(() => scroll)
    const { rerender } = renderTable(STOCKS)

    scroll = 300 // the user scrolled; every viewport-relative top shifted by -300
    rerender(
      <MemoryRouter>
        <StockTable stocks={[...STOCKS]} />
      </MemoryRouter>,
    )

    expect(screen.getAllByTestId('stock-slot').some((el) => el.classList.contains('is-moved'))).toBe(false)
  })

  it('only marks the rows whose rank actually changed', () => {
    stubLayout(() => 0)
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[0], STOCKS[2], STOCKS[1]]} />
      </MemoryRouter>,
    )

    const moved = screen.getAllByTestId('stock-slot').filter((el) => el.classList.contains('is-moved'))
    expect(moved.map((el) => el.getAttribute('data-ticker')).sort()).toEqual(['GSK.L', 'VOD.L'])
  })
})

/** Rank-delta chips are driven by list order alone, so these run without the rect stub. */
describe('StockTable rank-delta chips', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  const chipFor = (ticker: string) =>
    screen
      .getAllByTestId('stock-slot')
      .find((el) => el.getAttribute('data-ticker') === ticker)
      ?.querySelector('[data-testid="rank-delta"]') ?? null

  it('shows no chips on first render', () => {
    renderTable(STOCKS)

    expect(screen.queryAllByTestId('rank-delta')).toHaveLength(0)
  })

  it('labels each moved row with how many places it rose or fell, and nothing on rows that held', () => {
    const { rerender } = renderTable(STOCKS)

    // Four-row board: BP 3→0 (▲3), AZN 0→2 (▼2), VOD 2→3 (▼1), GSK holds at 1.
    const extra = makeStock({ ticker: 'BP.L', name: 'BP' })
    rerender(
      <MemoryRouter>
        <StockTable stocks={[...STOCKS, extra]} />
      </MemoryRouter>,
    )
    expect(screen.queryAllByTestId('rank-delta')).toHaveLength(0) // a new row is not a move

    rerender(
      <MemoryRouter>
        <StockTable stocks={[extra, STOCKS[1], STOCKS[0], STOCKS[2]]} />
      </MemoryRouter>,
    )

    expect(chipFor('BP.L')).toHaveTextContent('▲3')
    expect(chipFor('BP.L')).toHaveClass('rank-delta--up')
    expect(chipFor('GSK.L')).toBeNull()
    expect(chipFor('AZN.L')).toHaveTextContent('▼2')
    expect(chipFor('AZN.L')).toHaveClass('rank-delta--down')
    expect(chipFor('VOD.L')).toHaveTextContent('▼1')
  })

  it('removes the chips after the rank-delta window', () => {
    vi.useFakeTimers()
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[2], STOCKS[0], STOCKS[1]]} />
      </MemoryRouter>,
    )
    expect(screen.getAllByTestId('rank-delta')).toHaveLength(3)

    act(() => {
      vi.advanceTimersByTime(RANK_DELTA_MS - 1)
    })
    expect(screen.getAllByTestId('rank-delta')).toHaveLength(3)

    act(() => {
      vi.advanceTimersByTime(2)
    })
    expect(screen.queryAllByTestId('rank-delta')).toHaveLength(0)
  })

  it('a second re-rank inside the window replaces the chips and restarts the clock', () => {
    vi.useFakeTimers()
    const { rerender } = renderTable(STOCKS)

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[1], STOCKS[0], STOCKS[2]]} />
      </MemoryRouter>,
    )
    act(() => {
      vi.advanceTimersByTime(RANK_DELTA_MS / 2)
    })
    // Only VOD moves this time (to the top): AZN/GSK lose their chips, VOD gets ▲2.
    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[2], STOCKS[1], STOCKS[0]]} />
      </MemoryRouter>,
    )
    expect(chipFor('VOD.L')).toHaveTextContent('▲2')
    expect(chipFor('GSK.L')).toHaveTextContent('▼1')
    expect(chipFor('AZN.L')).toHaveTextContent('▼1')

    act(() => {
      vi.advanceTimersByTime(RANK_DELTA_MS / 2 + 1)
    })
    // Still within the restarted window.
    expect(screen.getAllByTestId('rank-delta')).toHaveLength(3)
    act(() => {
      vi.advanceTimersByTime(RANK_DELTA_MS / 2)
    })
    expect(screen.queryAllByTestId('rank-delta')).toHaveLength(0)
  })

  it('does not re-render rows that neither moved nor changed (memo stays effective)', () => {
    const { rerender } = renderTable(STOCKS)
    const before = screen.getAllByTestId('stock-row')

    rerender(
      <MemoryRouter>
        <StockTable stocks={[STOCKS[0], STOCKS[2], STOCKS[1]]} />
      </MemoryRouter>,
    )

    // Same DOM node for the unmoved row, and no chip on it.
    expect(screen.getAllByTestId('stock-row')[0]).toBe(before[0])
    expect(chipFor('AZN.L')).toBeNull()
  })
})
