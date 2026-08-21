import type { ReactNode } from 'react'
import type { FxRate } from '../../api/types'

export interface FxRateProviderProps {
  children: ReactNode
}

export type FxRateStatus = 'loading' | 'ready' | 'error'

/** What `useFxRate()` hands to consumers. `rate` is null until the first successful fetch. */
export interface FxRateState {
  status: FxRateStatus
  rate: FxRate | null
}
