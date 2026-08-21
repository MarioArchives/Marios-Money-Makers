import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RankDeltaChip } from './RankDeltaChip'

describe('RankDeltaChip', () => {
  it('shows a rise as ▲ plus the number of places', () => {
    render(<RankDeltaChip delta={3} />)

    const chip = screen.getByTestId('rank-delta')
    expect(chip).toHaveTextContent('▲3')
    expect(chip).toHaveClass('rank-delta--up')
    expect(chip).not.toHaveClass('rank-delta--down')
  })

  it('shows a fall as ▼ plus the number of places', () => {
    render(<RankDeltaChip delta={-2} />)

    const chip = screen.getByTestId('rank-delta')
    expect(chip).toHaveTextContent('▼2')
    expect(chip).toHaveClass('rank-delta--down')
  })

  it('renders nothing for no change', () => {
    const { container } = render(<RankDeltaChip delta={0} />)
    expect(container).toBeEmptyDOMElement()

    const { container: undef } = render(<RankDeltaChip />)
    expect(undef).toBeEmptyDOMElement()
  })

  it('is decorative: hidden from the accessibility tree', () => {
    render(<RankDeltaChip delta={1} />)

    expect(screen.getByTestId('rank-delta')).toHaveAttribute('aria-hidden', 'true')
  })
})
