import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AppProvidersProps } from './AppProviders.props'
import { FxRateProvider } from '../FxRateProvider/FxRateProvider'
import './AppProviders.css'

/** Builds the app-wide `QueryClient`: `retry: false` under Vitest so tests don't hang/retry against mocked failures; `refetchOnWindowFocus: false` since the app already polls on fixed intervals. */
function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: import.meta.env.MODE === 'test' ? false : 3,
        refetchOnWindowFocus: false,
      },
    },
  })
}

/** The single, slim, root-level provider: composes cross-cutting concerns (`QueryClientProvider`, `FxRateProvider`) around `children`. New concerns belong here, not as ad hoc providers elsewhere. */
export function AppProviders({ children }: AppProvidersProps): JSX.Element {
  const [queryClient] = useState(createQueryClient)

  return (
    <QueryClientProvider client={queryClient}>
      <FxRateProvider>{children}</FxRateProvider>
    </QueryClientProvider>
  )
}
