import { createContext, useContext, useMemo } from 'react'
import type { FxRateProviderProps, FxRateState } from './FxRateProvider.props'
import { useUsdGbpRateQuery } from '../../api/fx'
import './FxRateProvider.css'

/** Default for trees without a provider (unit tests): no rate, so GBP figures simply don't render. */
export const NO_FX_RATE: FxRateState = { status: 'loading', rate: null }

export const FxRateContext = createContext<FxRateState>(NO_FX_RATE)

/** Owns the single USD->GBP rate query and shares it via context so every price converts with the same rate from one subscription. Must sit inside `QueryClientProvider`. */
export function FxRateProvider({ children }: FxRateProviderProps): JSX.Element {
  const query = useUsdGbpRateQuery()
  const value = useMemo<FxRateState>(() => {
    if (query.data) {
      return { status: 'ready', rate: query.data }
    }
    if (query.isError) {
      return { status: 'error', rate: null }
    }
    return NO_FX_RATE
  }, [query.data, query.isError])

  return <FxRateContext.Provider value={value}>{children}</FxRateContext.Provider>
}

/** Current USD->GBP rate state; `rate` is null while loading/unavailable. */
export function useFxRate(): FxRateState {
  return useContext(FxRateContext)
}
