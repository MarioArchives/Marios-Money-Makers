import { afterEach, describe, expect, it, vi } from 'vitest'
import { FX_API_URL, FX_REFRESH_MS, fetchUsdGbpRate, fxRateKey } from './fx'

function mockFetch(body: unknown, ok = true, status = 200): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok, status, json: async () => body })),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchUsdGbpRate', () => {
  it('parses the Frankfurter `latest` shape into an FxRate', async () => {
    mockFetch({ amount: 1, base: 'USD', date: '2026-08-20', rates: { GBP: 0.73388 } })

    const rate = await fetchUsdGbpRate()

    expect(fetch).toHaveBeenCalledWith(FX_API_URL)
    expect(rate).toEqual({ base: 'USD', quote: 'GBP', rate: 0.73388, date: '2026-08-20', source: 'ECB' })
  })

  it('rejects on a non-2xx response', async () => {
    mockFetch({}, false, 503)

    await expect(fetchUsdGbpRate()).rejects.toThrow(/503/)
  })

  it('rejects when the payload has no usable GBP rate', async () => {
    mockFetch({ date: '2026-08-20', rates: {} })
    await expect(fetchUsdGbpRate()).rejects.toThrow(/GBP/)

    mockFetch({ date: '2026-08-20', rates: { GBP: -1 } })
    await expect(fetchUsdGbpRate()).rejects.toThrow(/GBP/)
  })
})

describe('FX query configuration', () => {
  it('uses a dedicated cache key and an hourly refresh', () => {
    expect(fxRateKey).toEqual(['fx', 'USD', 'GBP'])
    expect(FX_REFRESH_MS).toBe(60 * 60 * 1000)
  })

  it('defaults to the Frankfurter USD->GBP endpoint', () => {
    expect(FX_API_URL).toContain('frankfurter')
    expect(FX_API_URL).toContain('base=USD')
    expect(FX_API_URL).toContain('symbols=GBP')
  })
})
