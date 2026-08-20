import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ErrorBadge, STALE_LABEL } from './ErrorBadge'

/**
 * v3 error UX: this component no longer renders verbose per-row error text.
 * A degraded stock is communicated by greying its row (see StockRow) plus
 * the single muted dot rendered here. Each assertion below replaces the
 * equivalent pre-v3 badge assertion.
 */
describe('ErrorBadge', () => {
  it('renders nothing when fresh and error-free', () => {
    const { container } = render(<ErrorBadge isStale={false} error={null} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders a subtle stale dot when isStale is true and there is no error', () => {
    render(<ErrorBadge isStale={true} error={null} />)

    const dot = screen.getByTestId('stale-dot')
    expect(dot.classList.contains('stale-dot')).toBe(true)
  })

  it('renders the same subtle dot when an error is set', () => {
    render(<ErrorBadge isStale={false} error="fetch failed for AZN.L" />)

    expect(screen.getByTestId('stale-dot')).toBeInTheDocument()
  })

  // Replaces the pre-v3 "includes the error text" assertion, inverted to
  // encode the new rule: the backend's error string must never reach the UI.
  it('never surfaces the backend error string as text, title or label', () => {
    render(<ErrorBadge isStale={true} error="fetch failed for AZN.L" />)

    const dot = screen.getByTestId('stale-dot')
    const surfaced = [
      dot.textContent ?? '',
      dot.getAttribute('title') ?? '',
      dot.getAttribute('aria-label') ?? '',
    ].join(' ')

    expect(dot.textContent).toBe('')
    expect(surfaced).not.toContain('fetch failed for AZN.L')
    expect(screen.queryByText(/fetch failed/i)).not.toBeInTheDocument()
  })

  it('carries a calm, fixed accessible label explaining the greyed figures', () => {
    render(<ErrorBadge isStale={true} error={null} />)

    expect(screen.getByLabelText(STALE_LABEL)).toBeInTheDocument()
    expect(STALE_LABEL).toBe('Showing last known price')
  })
})
