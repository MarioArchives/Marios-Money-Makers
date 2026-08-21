import { describe, expect, it } from 'vitest'
import { sortByChangeDesc } from './sortStocks'
import type { StockSummary } from '../api/types'

function stock(ticker: string, changePercent: number | null): StockSummary {
  return {
    ticker,
    name: ticker,
    sector: 'x',
    price: 1,
    currency: 'USD',
    previous_close: 1,
    change: changePercent,
    change_percent: changePercent,
    is_stale: false,
    error: null,
  }
}

describe('sortByChangeDesc', () => {
  it('orders by change_percent, highest first', () => {
    const sorted = sortByChangeDesc([stock('A', -1.2), stock('B', 3.4), stock('C', 0.5)])
    expect(sorted.map((s) => s.ticker)).toEqual(['B', 'C', 'A'])
  })

  it('sinks stocks without a change figure to the bottom, in ticker order', () => {
    const sorted = sortByChangeDesc([stock('Z', null), stock('A', null), stock('M', -5)])
    expect(sorted.map((s) => s.ticker)).toEqual(['M', 'A', 'Z'])
  })

  it('breaks ties by ticker so equal movers never swap between polls', () => {
    const sorted = sortByChangeDesc([stock('MSFT', 1), stock('AAPL', 1), stock('NVDA', 1)])
    expect(sorted.map((s) => s.ticker)).toEqual(['AAPL', 'MSFT', 'NVDA'])
  })

  it('does not mutate the input and keeps the same object references', () => {
    const input = [stock('A', 1), stock('B', 2)]
    const sorted = sortByChangeDesc(input)
    expect(input.map((s) => s.ticker)).toEqual(['A', 'B'])
    expect(sorted[0]).toBe(input[1])
    expect(sorted[1]).toBe(input[0])
  })
})
