import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TopMoversCard } from './TopMoversCard'

describe('TopMoversCard', () => {
  it('renders a "Top Movers" heading', () => {
    render(<TopMoversCard />)

    expect(screen.getByRole('heading', { name: /top movers/i })).toBeInTheDocument()
  })

  it('renders a visible "Coming soon" marker', () => {
    render(<TopMoversCard />)

    expect(screen.getByText(/coming soon/i)).toBeInTheDocument()
  })

  it('makes no network requests', () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    render(<TopMoversCard />)

    expect(fetchSpy).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
