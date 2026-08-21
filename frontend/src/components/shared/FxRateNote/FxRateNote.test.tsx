import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FX_UNAVAILABLE_MESSAGE, FxRateNote } from './FxRateNote'
import { FxRateContext } from '../../../providers/FxRateProvider/FxRateProvider'
import type { FxRateState } from '../../../providers/FxRateProvider/FxRateProvider.props'

function renderWith(state: FxRateState) {
  return render(
    <FxRateContext.Provider value={state}>
      <FxRateNote />
    </FxRateContext.Provider>,
  )
}

describe('FxRateNote', () => {
  it('renders nothing while the rate is loading', () => {
    renderWith({ status: 'loading', rate: null })

    expect(screen.queryByTestId('fx-rate-note')).not.toBeInTheDocument()
  })

  it('discloses the rate, its source and date once ready', () => {
    renderWith({
      status: 'ready',
      rate: { base: 'USD', quote: 'GBP', rate: 0.73388, date: '2026-08-20', source: 'ECB' },
    })

    expect(screen.getByTestId('fx-rate-note').textContent).toBe('1 USD = 0.7339 GBP · ECB, 2026-08-20')
  })

  it('says the rate is unavailable when the fetch failed', () => {
    renderWith({ status: 'error', rate: null })

    const note = screen.getByTestId('fx-rate-note')
    expect(note.textContent).toBe(FX_UNAVAILABLE_MESSAGE)
    expect(note.classList.contains('is-unavailable')).toBe(true)
  })
})
