import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'

describe('DashboardPage', () => {
  it('renders the three placeholder cards', () => {
    render(<DashboardPage />)

    expect(screen.getByRole('heading', { name: /market summary/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /sector breakdown/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /top movers/i })).toBeInTheDocument()
  })

  it('renders a "Coming soon" marker for each of the three cards', () => {
    render(<DashboardPage />)

    expect(screen.getAllByText(/coming soon/i)).toHaveLength(3)
  })

  it('makes no network requests', () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    render(<DashboardPage />)

    expect(fetchSpy).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
