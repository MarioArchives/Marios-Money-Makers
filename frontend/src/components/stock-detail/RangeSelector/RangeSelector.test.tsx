import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RangeSelector } from './RangeSelector'

describe('RangeSelector', () => {
  it('renders one option per backend range', () => {
    render(<RangeSelector value="1d" onChange={() => {}} />)

    expect(screen.getByRole('button', { name: '1D' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '30D' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ALL' })).toBeInTheDocument()
  })

  it('marks only the current value as pressed', () => {
    render(<RangeSelector value="30d" onChange={() => {}} />)

    expect(screen.getByRole('button', { name: '30D' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '1D' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'ALL' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('reports a newly picked range through onChange', () => {
    const onChange = vi.fn()
    render(<RangeSelector value="1d" onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'ALL' }))

    expect(onChange).toHaveBeenCalledWith('all')
  })

  it('does not fire onChange when the already-selected range is clicked again', () => {
    const onChange = vi.fn()
    render(<RangeSelector value="1d" onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: '1D' }))

    expect(onChange).not.toHaveBeenCalled()
  })
})
