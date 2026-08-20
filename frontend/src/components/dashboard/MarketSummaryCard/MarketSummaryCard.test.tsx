import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MarketSummaryCard } from './MarketSummaryCard'

describe('MarketSummaryCard', () => {
  it('renders a "Market Summary" heading', () => {
    render(<MarketSummaryCard />)

    expect(screen.getByRole('heading', { name: /market summary/i })).toBeInTheDocument()
  })

  it('renders a visible "Coming soon" marker', () => {
    render(<MarketSummaryCard />)

    expect(screen.getByText(/coming soon/i)).toBeInTheDocument()
  })

  it('makes no network requests', () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    render(<MarketSummaryCard />)

    expect(fetchSpy).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
