import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SectorBreakdownCard } from './SectorBreakdownCard'

describe('SectorBreakdownCard', () => {
  it('renders a "Sector Breakdown" heading', () => {
    render(<SectorBreakdownCard />)

    expect(screen.getByRole('heading', { name: /sector breakdown/i })).toBeInTheDocument()
  })

  it('renders a visible "Coming soon" marker', () => {
    render(<SectorBreakdownCard />)

    expect(screen.getByText(/coming soon/i)).toBeInTheDocument()
  })

  it('makes no network requests', () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    render(<SectorBreakdownCard />)

    expect(fetchSpy).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
