import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AppProvidersProps } from './AppProviders.props'
import './AppProviders.css'

/**
 * Builds the single `QueryClient` used app-wide.
 *
 * - `retry: false` under Vitest (`import.meta.env.MODE === 'test'`) so
 *   tests don't hang/retry against mocked failures.
 * - `refetchOnWindowFocus: false` everywhere — this app already polls on
 *   fixed intervals (see `api/queries.ts`), so a focus-triggered refetch
 *   would just be redundant traffic.
 */
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

/**
 * The single, slim, root-level global provider for the app. Composes
 * cross-cutting concerns — currently just `QueryClientProvider` — around
 * `children`. Kept minimal by design: new cross-cutting concerns should be
 * added here rather than as additional ad hoc providers scattered around
 * the tree.
 */
export function AppProviders({ children }: AppProvidersProps): JSX.Element {
  const [queryClient] = useState(createQueryClient)

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
