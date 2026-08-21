import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseQueryResult } from '@tanstack/react-query'
import { FxRateContext, FxRateProvider, NO_FX_RATE, useFxRate } from './FxRateProvider'
import { useUsdGbpRateQuery } from '../../api/fx'
import type { FxRate } from '../../api/types'

vi.mock('../../api/fx', () => ({
  useUsdGbpRateQuery: vi.fn(),
}))

const mockedQuery = vi.mocked(useUsdGbpRateQuery)

function queryResult(partial: Partial<UseQueryResult<FxRate, Error>>): UseQueryResult<FxRate, Error> {
  return { data: undefined, isError: false, ...partial } as UseQueryResult<FxRate, Error>
}

function Probe(): JSX.Element {
  const { status, rate } = useFxRate()
  return <div data-testid="probe">{`${status}|${rate ? rate.rate : 'none'}`}</div>
}

beforeEach(() => {
  mockedQuery.mockReset()
})

describe('FxRateProvider', () => {
  it('exposes loading with no rate before the query resolves', () => {
    mockedQuery.mockReturnValue(queryResult({}))

    render(
      <FxRateProvider>
        <Probe />
      </FxRateProvider>,
    )

    expect(screen.getByTestId('probe').textContent).toBe('loading|none')
  })

  it('exposes the fetched rate as ready', () => {
    mockedQuery.mockReturnValue(
      queryResult({ data: { base: 'USD', quote: 'GBP', rate: 0.75, date: '2026-08-20', source: 'ECB' } }),
    )

    render(
      <FxRateProvider>
        <Probe />
      </FxRateProvider>,
    )

    expect(screen.getByTestId('probe').textContent).toBe('ready|0.75')
  })

  it('exposes error with no rate when the query failed and nothing was ever fetched', () => {
    mockedQuery.mockReturnValue(queryResult({ isError: true }))

    render(
      <FxRateProvider>
        <Probe />
      </FxRateProvider>,
    )

    expect(screen.getByTestId('probe').textContent).toBe('error|none')
  })

  it('keeps serving the last good rate if a refresh fails (React Query retains data)', () => {
    mockedQuery.mockReturnValue(
      queryResult({
        data: { base: 'USD', quote: 'GBP', rate: 0.75, date: '2026-08-20', source: 'ECB' },
        isError: true,
      }),
    )

    render(
      <FxRateProvider>
        <Probe />
      </FxRateProvider>,
    )

    expect(screen.getByTestId('probe').textContent).toBe('ready|0.75')
  })
})

describe('useFxRate without a provider', () => {
  it('falls back to the no-rate default so consumers render native figures', () => {
    render(<Probe />)

    expect(screen.getByTestId('probe').textContent).toBe('loading|none')
    expect(NO_FX_RATE).toEqual({ status: 'loading', rate: null })
  })

  it('reads whatever a raw context provider supplies (test seam)', () => {
    render(
      <FxRateContext.Provider
        value={{ status: 'ready', rate: { base: 'USD', quote: 'GBP', rate: 0.5, date: 'd', source: 's' } }}
      >
        <Probe />
      </FxRateContext.Provider>,
    )

    expect(screen.getByTestId('probe').textContent).toBe('ready|0.5')
  })
})
