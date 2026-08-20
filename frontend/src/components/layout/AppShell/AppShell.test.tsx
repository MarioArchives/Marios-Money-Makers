import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from './AppShell'

function renderShellAtRoute(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<div>leaderboard-outlet-content</div>} />
          <Route path="/dashboard" element={<div>dashboard-outlet-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('AppShell', () => {
  it('renders a nav link to the leaderboard route ("/")', () => {
    renderShellAtRoute('/')

    expect(screen.getByRole('link', { name: /leaderboard/i })).toHaveAttribute('href', '/')
  })

  it('renders a nav link to the dashboard route ("/dashboard")', () => {
    renderShellAtRoute('/')

    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute(
      'href',
      '/dashboard',
    )
  })

  it('renders exactly two nav links', () => {
    renderShellAtRoute('/')

    expect(screen.getAllByRole('link')).toHaveLength(2)
  })

  // Wireframe: header bar with a left menu slot and a centred title. The
  // title is plain text, not a third link, so the nav-link count above holds.
  it('renders the centred masthead title in the header bar', () => {
    renderShellAtRoute('/')

    expect(screen.getByTestId('app-title')).toHaveTextContent("Mario's Money Makers")
    expect(screen.getByTestId('app-logo')).toHaveTextContent('M3')
    expect(screen.getAllByRole('link')).toHaveLength(2)
  })

  it('sticks and condenses on scroll: hides the title, keeps the logo', () => {
    renderShellAtRoute('/')
    const bar = screen.getByRole('banner')

    expect(bar.classList.contains('is-condensed')).toBe(false)

    Object.defineProperty(window, 'scrollY', { value: 120, writable: true, configurable: true })
    fireEvent.scroll(window)

    expect(bar.classList.contains('is-condensed')).toBe(true)
    expect(screen.getByTestId('app-logo')).toBeInTheDocument()

    Object.defineProperty(window, 'scrollY', { value: 0, writable: true, configurable: true })
    fireEvent.scroll(window)

    expect(bar.classList.contains('is-condensed')).toBe(false)
  })

  it('renders the matched child route content via Outlet at "/"', () => {
    renderShellAtRoute('/')

    expect(screen.getByText('leaderboard-outlet-content')).toBeInTheDocument()
    expect(screen.queryByText('dashboard-outlet-content')).not.toBeInTheDocument()
  })

  it('renders the matched child route content via Outlet at "/dashboard"', () => {
    renderShellAtRoute('/dashboard')

    expect(screen.getByText('dashboard-outlet-content')).toBeInTheDocument()
    expect(screen.queryByText('leaderboard-outlet-content')).not.toBeInTheDocument()
  })
})
