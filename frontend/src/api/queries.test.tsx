import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import {
  POLL_INTERVAL_MS,
  STALE_TIME_MS,
  historyKey,
  stockKey,
  stocksKey,
  storedKey,
  useStockDetailQuery,
  useStockHistoryQuery,
  useStocksQuery,
  useStoredDataQuery,
} from './queries'
import { apiGet } from './client'
import type { HistoryResponse, StockSummary, StocksResponse, StoredDataResponse } from './types'

vi.mock('./client', () => ({
  apiGet: vi.fn(),
}))

const mockedApiGet = vi.mocked(apiGet)

function createWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
}

const aznSummary: StockSummary = {
  ticker: 'AZN.L',
  name: 'AstraZeneca',
  sector: 'Pharma',
  price: 112.34,
  currency: 'GBP',
  previous_close: 110.0,
  change: 2.34,
  change_percent: 2.13,
  is_stale: false,
  error: null,
}

const gskSummary: StockSummary = {
  ticker: 'GSK.L',
  name: 'GSK',
  sector: 'Pharma',
  price: 15.5,
  currency: 'GBP',
  previous_close: 16.0,
  change: -0.5,
  change_percent: -3.13,
  is_stale: false,
  error: null,
}

const mockStocksResponse: StocksResponse = {
  updated_at: '2026-08-19T12:00:00Z',
  stocks: [aznSummary, gskSummary],
}

const mockDetailByTicker: Record<string, StockSummary> = {
  'AZN.L': aznSummary,
  'GSK.L': gskSummary,
}

const mockHistoryByTicker: Record<string, HistoryResponse> = {
  'AZN.L': {
    ticker: 'AZN.L',
    interval: '5m',
    range: '1d',
    points: [
      { t: '2026-08-19T12:00:00Z', close: 112.0 },
      { t: '2026-08-19T12:05:00Z', close: 112.34 },
    ],
    is_stale: false,
    error: null,
  },
  'GSK.L': {
    ticker: 'GSK.L',
    interval: '5m',
    range: '1d',
    points: [
      { t: '2026-08-19T12:00:00Z', close: 15.7 },
      { t: '2026-08-19T12:05:00Z', close: 15.5 },
    ],
    is_stale: false,
    error: null,
  },
}

describe('query key builders', () => {
  it('builds a fixed key for the stocks leaderboard', () => {
    expect(stocksKey).toEqual(['stocks'])
  })

  it('builds ticker-scoped keys for stock detail', () => {
    expect(stockKey('AZN.L')).toEqual(['stock', 'AZN.L'])
    expect(stockKey('GSK.L')).toEqual(['stock', 'GSK.L'])
    expect(stockKey('AZN.L')).not.toEqual(stockKey('GSK.L'))
  })

  it('builds ticker- and range-scoped keys for stock history, nested under the stock key', () => {
    expect(historyKey('AZN.L')).toEqual(['stock', 'AZN.L', 'history', '1d'])
    expect(historyKey('AZN.L', 'all')).toEqual(['stock', 'AZN.L', 'history', 'all'])
    expect(historyKey('AZN.L')).not.toEqual(historyKey('GSK.L'))
    expect(historyKey('AZN.L')).not.toEqual(historyKey('AZN.L', '30d'))
    expect(historyKey('AZN.L')).not.toEqual(stockKey('AZN.L'))
  })

  it('builds ticker- and tier-scoped keys for stored data, distinct from history keys', () => {
    expect(storedKey('AAPL', 'minute')).toEqual(['stock', 'AAPL', 'stored', 'minute'])
    expect(storedKey('AAPL', 'month')).not.toEqual(storedKey('AAPL', 'minute'))
    expect(storedKey('AAPL', 'minute')).not.toEqual(historyKey('AAPL', '1d'))
  })
})

describe('useStoredDataQuery', () => {
  beforeEach(() => {
    mockedApiGet.mockReset()
  })

  it('fetches the stored endpoint for the requested tier and caches per tier', async () => {
    const stored: StoredDataResponse = {
      ticker: 'AAPL',
      tier: 'hour',
      table: 'bars_hour',
      currency: 'USD',
      last_fetch_at: '2026-08-20T11:00:00Z',
      counts: { minute: 3, hour: 1, month: 0 },
      rows: [
        {
          ts: '2026-08-20T10:00:00Z',
          price: 230.5,
          analytics: { o: 230, h: 231, l: 229, c: 230.5, v: 1000, vw: 230.2, n: 10 },
          recorded_at: '2026-08-20T11:00:00Z',
        },
      ],
    }
    mockedApiGet.mockResolvedValueOnce(stored)
    const client = makeQueryClient()

    const { result } = renderHook(() => useStoredDataQuery('AAPL', 'hour'), {
      wrapper: createWrapper(client),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockedApiGet).toHaveBeenCalledWith('/api/stocks/AAPL/stored?tier=hour')
    expect(client.getQueryData(storedKey('AAPL', 'hour'))).toEqual(stored)
    expect(client.getQueryData(storedKey('AAPL', 'minute'))).toBeUndefined()
    const query = client.getQueryCache().find({ queryKey: storedKey('AAPL', 'hour') })
    expect((query?.options as { refetchInterval?: number } | undefined)?.refetchInterval).toBe(
      POLL_INTERVAL_MS,
    )
  })
})

describe('polling configuration constants', () => {
  it('exposes a 20s poll interval, matching the plan', () => {
    expect(POLL_INTERVAL_MS).toBe(20000)
  })

  it('exposes a staleTime shorter than the poll interval', () => {
    expect(STALE_TIME_MS).toBeLessThan(POLL_INTERVAL_MS)
  })
})

describe('useStocksQuery', () => {
  beforeEach(() => {
    mockedApiGet.mockReset()
  })

  it('resolves the mocked API payload into the expected StocksResponse shape', async () => {
    mockedApiGet.mockResolvedValueOnce(mockStocksResponse)
    const client = makeQueryClient()

    const { result } = renderHook(() => useStocksQuery(), { wrapper: createWrapper(client) })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual(mockStocksResponse)
    expect(mockedApiGet).toHaveBeenCalledWith('/api/stocks')
  })

  it('is registered under the stocks query key with a 20s refetchInterval', async () => {
    mockedApiGet.mockResolvedValueOnce(mockStocksResponse)
    const client = makeQueryClient()

    renderHook(() => useStocksQuery(), { wrapper: createWrapper(client) })

    await waitFor(() => {
      const query = client.getQueryCache().find({ queryKey: stocksKey })
      expect(query).toBeDefined()
      expect((query?.options as { refetchInterval?: number } | undefined)?.refetchInterval).toBe(
        POLL_INTERVAL_MS,
      )
    })
  })
})

describe('useStockDetailQuery / useStockHistoryQuery ticker isolation', () => {
  beforeEach(() => {
    mockedApiGet.mockReset()
    mockedApiGet.mockImplementation(async (path: unknown) => {
      const p = String(path)
      for (const [ticker, detail] of Object.entries(mockDetailByTicker)) {
        if (p === `/api/stocks/${ticker}`) return detail
      }
      for (const [ticker, history] of Object.entries(mockHistoryByTicker)) {
        if (p === `/api/stocks/${ticker}/history?range=1d`) return history
      }
      throw new Error(`unexpected path: ${p}`)
    })
  })

  it('caches detail data independently for two different tickers under one QueryClientProvider', async () => {
    const client = makeQueryClient()
    const wrapper = createWrapper(client)

    const azn = renderHook(() => useStockDetailQuery('AZN.L'), { wrapper })
    const gsk = renderHook(() => useStockDetailQuery('GSK.L'), { wrapper })

    await waitFor(() => expect(azn.result.current.isSuccess).toBe(true))
    await waitFor(() => expect(gsk.result.current.isSuccess).toBe(true))

    expect(azn.result.current.data).toEqual(aznSummary)
    expect(gsk.result.current.data).toEqual(gskSummary)
    expect(azn.result.current.data).not.toEqual(gsk.result.current.data)

    expect(client.getQueryData(stockKey('AZN.L'))).toEqual(aznSummary)
    expect(client.getQueryData(stockKey('GSK.L'))).toEqual(gskSummary)
  })

  it('caches history data independently for two different tickers, distinct from their detail keys', async () => {
    const client = makeQueryClient()
    const wrapper = createWrapper(client)

    const aznHistory = renderHook(() => useStockHistoryQuery('AZN.L'), { wrapper })
    const gskHistory = renderHook(() => useStockHistoryQuery('GSK.L'), { wrapper })

    await waitFor(() => expect(aznHistory.result.current.isSuccess).toBe(true))
    await waitFor(() => expect(gskHistory.result.current.isSuccess).toBe(true))

    expect(aznHistory.result.current.data).toEqual(mockHistoryByTicker['AZN.L'])
    expect(gskHistory.result.current.data).toEqual(mockHistoryByTicker['GSK.L'])
    expect(aznHistory.result.current.data).not.toEqual(gskHistory.result.current.data)

    expect(client.getQueryData(historyKey('AZN.L'))).toEqual(mockHistoryByTicker['AZN.L'])
    expect(client.getQueryData(historyKey('GSK.L'))).toEqual(mockHistoryByTicker['GSK.L'])

    // History cache entries are keyed independently of the corresponding detail entries.
    expect(client.getQueryCache().find({ queryKey: historyKey('AZN.L') })).not.toBe(
      client.getQueryCache().find({ queryKey: stockKey('AZN.L') }),
    )
  })

  it('sends the requested range to the API and keys the cache per range', async () => {
    mockedApiGet.mockReset()
    mockedApiGet.mockImplementation(async (path: unknown) => {
      const p = String(path)
      if (p === '/api/stocks/AZN.L/history?range=30d') {
        return { ...mockHistoryByTicker['AZN.L'], range: '30d', interval: '1h' }
      }
      if (p === '/api/stocks/AZN.L/history?range=1d') return mockHistoryByTicker['AZN.L']
      throw new Error(`unexpected path: ${p}`)
    })
    const client = makeQueryClient()
    const wrapper = createWrapper(client)

    const intraday = renderHook(() => useStockHistoryQuery('AZN.L'), { wrapper })
    const monthly = renderHook(() => useStockHistoryQuery('AZN.L', '30d'), { wrapper })

    await waitFor(() => expect(intraday.result.current.isSuccess).toBe(true))
    await waitFor(() => expect(monthly.result.current.isSuccess).toBe(true))

    expect(mockedApiGet).toHaveBeenCalledWith('/api/stocks/AZN.L/history?range=1d')
    expect(mockedApiGet).toHaveBeenCalledWith('/api/stocks/AZN.L/history?range=30d')
    expect(historyKey('AZN.L')).toEqual(['stock', 'AZN.L', 'history', '1d'])
    expect(historyKey('AZN.L', '30d')).toEqual(['stock', 'AZN.L', 'history', '30d'])
    expect(client.getQueryData(historyKey('AZN.L', '30d'))).toMatchObject({ range: '30d' })
    expect(client.getQueryData(historyKey('AZN.L'))).toMatchObject({ range: '1d' })
  })

  it('does not cross-contaminate the cache when only one ticker has resolved', async () => {
    const client = makeQueryClient()
    const wrapper = createWrapper(client)

    const azn = renderHook(() => useStockDetailQuery('AZN.L'), { wrapper })
    await waitFor(() => expect(azn.result.current.isSuccess).toBe(true))

    // GSK.L was never rendered/fetched — its cache entry must not exist yet.
    expect(client.getQueryData(stockKey('GSK.L'))).toBeUndefined()
    expect(client.getQueryCache().find({ queryKey: stockKey('GSK.L') })).toBeUndefined()
  })
})
