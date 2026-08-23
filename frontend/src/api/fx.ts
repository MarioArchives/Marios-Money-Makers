import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import type { FxRate } from './types'

/** USD->GBP reference rate, fetched client-side from the Frankfurter API (see README.md). Override with `VITE_FX_API_URL`; must return Frankfurter's `latest` shape. */
export const FX_API_URL =
  import.meta.env.VITE_FX_API_URL ?? 'https://api.frankfurter.dev/v1/latest?base=USD&symbols=GBP'

export const FX_SOURCE = 'ECB'

/** ECB rates are published once per working day; an hourly check is plenty. */
export const FX_REFRESH_MS = 60 * 60 * 1000

export const fxRateKey = ['fx', 'USD', 'GBP'] as const

interface FrankfurterLatest {
  date?: unknown
  rates?: Record<string, unknown>
}

export async function fetchUsdGbpRate(): Promise<FxRate> {
  const res = await fetch(FX_API_URL)
  if (!res.ok) {
    throw new Error(`FX API error ${res.status}`)
  }
  const body = (await res.json()) as FrankfurterLatest
  const rate = body.rates?.GBP
  if (typeof rate !== 'number' || !Number.isFinite(rate) || rate <= 0) {
    throw new Error('FX API response has no usable GBP rate')
  }
  return {
    base: 'USD',
    quote: 'GBP',
    rate,
    date: typeof body.date === 'string' ? body.date : '',
    source: FX_SOURCE,
  }
}

/** The one FX query in the app; on failure React Query keeps the last good rate so a transient outage never blanks the GBP figures on screen. */
export function useUsdGbpRateQuery(): UseQueryResult<FxRate, Error> {
  return useQuery({
    queryKey: fxRateKey,
    queryFn: fetchUsdGbpRate,
    staleTime: FX_REFRESH_MS,
    refetchInterval: FX_REFRESH_MS,
  })
}
