import { describe, expect, it } from 'vitest'
import { DISPLAY_CURRENCY, convertToGbp, formatCurrency, formatDisplayPrice } from './currency'

// en-US locale is pinned inside formatCurrency so output is deterministic
// regardless of the machine/browser locale.
describe('formatCurrency', () => {
  it('formats USD amounts with a dollar sign and two decimals', () => {
    expect(formatCurrency(316.9, 'USD')).toBe('$316.90')
    expect(formatCurrency(1234.5, 'USD')).toBe('$1,234.50')
    expect(formatCurrency(0, 'USD')).toBe('$0.00')
  })

  it('formats other currencies by their code', () => {
    expect(formatCurrency(1234.5, 'GBP')).toBe('£1,234.50')
  })

  it('formats negative amounts', () => {
    expect(formatCurrency(-2.5, 'USD')).toBe('-$2.50')
  })

  it('returns the same result on repeated calls (cached formatter)', () => {
    expect(formatCurrency(9.99, 'USD')).toBe(formatCurrency(9.99, 'USD'))
  })
})

const RATE = { base: 'USD', quote: 'GBP', rate: 0.5, date: '2026-08-20', source: 'ECB' } as const

describe('convertToGbp', () => {
  it('multiplies USD amounts by the USD->GBP rate', () => {
    expect(convertToGbp(200, 'USD', RATE)).toBe(100)
  })

  it('returns GBP amounts unchanged, even without a rate', () => {
    expect(convertToGbp(12.5, 'GBP', RATE)).toBe(12.5)
    expect(convertToGbp(12.5, 'GBP', null)).toBe(12.5)
  })

  it('returns null when there is no rate yet or the currency is not the rate base', () => {
    expect(convertToGbp(200, 'USD', null)).toBeNull()
    expect(convertToGbp(200, 'EUR', RATE)).toBeNull()
  })
})

describe('formatDisplayPrice', () => {
  it('formats in GBP when convertible', () => {
    expect(formatDisplayPrice(1234.56, 'USD', RATE)).toBe('£617.28')
  })

  it('falls back to the native currency when no rate is available', () => {
    expect(formatDisplayPrice(1234.56, 'USD', null)).toBe('$1,234.56')
    expect(formatDisplayPrice(1234.56, 'EUR', RATE)).toBe('€1,234.56')
  })

  it('exposes GBP as the display currency', () => {
    expect(DISPLAY_CURRENCY).toBe('GBP')
  })
})
