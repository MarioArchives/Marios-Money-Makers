import type { FxRate } from '../api/types'

/** Currency formatting driven by the API's `currency` field. `Intl.NumberFormat` instances are cached per code (expensive to construct inside 20s-poll re-renders); en-US locale is pinned for deterministic output. */
const formatters = new Map<string, Intl.NumberFormat>()

export function formatCurrency(value: number, currency: string): string {
  let formatter = formatters.get(currency)
  if (!formatter) {
    formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency })
    formatters.set(currency, formatter)
  }
  return formatter.format(value)
}

/** Converts `value` in `currency` to GBP: already GBP -> unchanged; `rate.base` (USD) -> multiplied by `rate.rate`; anything else or no rate yet -> `null` (callers render nothing rather than guess). */
export function convertToGbp(value: number, currency: string, rate: FxRate | null): number | null {
  if (currency === 'GBP') {
    return value
  }
  if (rate && currency === rate.base) {
    return value * rate.rate
  }
  return null
}

/** Display currency for every price in the UI. */
export const DISPLAY_CURRENCY = 'GBP'

/** Native-currency amount for display: GBP when the rate allows it, otherwise native currency, so figures never vanish while the rate is loading/unavailable (`FxRateNote` discloses that state). */
export function formatDisplayPrice(value: number, currency: string, rate: FxRate | null): string {
  const gbp = convertToGbp(value, currency, rate)
  return gbp === null ? formatCurrency(value, currency) : formatCurrency(gbp, DISPLAY_CURRENCY)
}
