import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useQueryClient } from '@tanstack/react-query'
import { AppProviders } from './AppProviders'

// AppProviders mounts FxRateProvider, whose query would otherwise call the
// real FX endpoint from inside these tests.
vi.mock('../../api/fx', () => ({
  useUsdGbpRateQuery: vi.fn(() => ({ data: undefined, isError: false })),
}))

/** Reads the QueryClient out of context to prove it was actually provided. */
function QueryClientProbe(): JSX.Element {
  const client = useQueryClient()
  return <div>query-client:{client ? 'present' : 'missing'}</div>
}

describe('AppProviders', () => {
  it('renders a single child inside the provider tree', () => {
    render(
      <AppProviders>
        <div>child-content</div>
      </AppProviders>,
    )

    expect(screen.getByText('child-content')).toBeInTheDocument()
  })

  it('renders multiple children inside the provider tree', () => {
    render(
      <AppProviders>
        <div>first-child</div>
        <div>second-child</div>
      </AppProviders>,
    )

    expect(screen.getByText('first-child')).toBeInTheDocument()
    expect(screen.getByText('second-child')).toBeInTheDocument()
  })

  it('makes a QueryClient resolvable via useQueryClient in descendants', () => {
    render(
      <AppProviders>
        <QueryClientProbe />
      </AppProviders>,
    )

    expect(screen.getByText('query-client:present')).toBeInTheDocument()
  })

  it('does not throw when rendered without QueryClientProvider already present higher up', () => {
    expect(() =>
      render(
        <AppProviders>
          <div>ok</div>
        </AppProviders>,
      ),
    ).not.toThrow()
  })
})
