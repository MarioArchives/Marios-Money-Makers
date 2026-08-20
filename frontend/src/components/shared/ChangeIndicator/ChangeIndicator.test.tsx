import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ChangeIndicator } from './ChangeIndicator'

describe('ChangeIndicator', () => {
  it('renders an up arrow with the up class for a positive change', () => {
    render(<ChangeIndicator changePercent={2.53} />)

    const indicator = screen.getByTestId('change-indicator')
    expect(indicator.classList.contains('up')).toBe(true)
    expect(indicator.textContent).toContain('▲')
  })

  it('renders a down arrow with the down class for a negative change', () => {
    render(<ChangeIndicator changePercent={-1.2} />)

    const indicator = screen.getByTestId('change-indicator')
    expect(indicator.classList.contains('down')).toBe(true)
    expect(indicator.textContent).toContain('▼')
  })

  it('renders a neutral state for a zero change', () => {
    render(<ChangeIndicator changePercent={0} />)

    const indicator = screen.getByTestId('change-indicator')
    expect(indicator.classList.contains('neutral')).toBe(true)
    expect(indicator.classList.contains('up')).toBe(false)
    expect(indicator.classList.contains('down')).toBe(false)
  })

  it('renders a neutral state when the change is null', () => {
    render(<ChangeIndicator changePercent={null} />)

    const indicator = screen.getByTestId('change-indicator')
    expect(indicator.classList.contains('neutral')).toBe(true)
    expect(indicator.classList.contains('up')).toBe(false)
    expect(indicator.classList.contains('down')).toBe(false)
  })
})
