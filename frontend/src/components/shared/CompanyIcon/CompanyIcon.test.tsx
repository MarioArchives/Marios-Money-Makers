import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { CompanyIcon } from './CompanyIcon'

describe('CompanyIcon', () => {
  it('renders an img with src derived from the ticker and alt set to the company name', () => {
    render(<CompanyIcon ticker="AZN.L" name="AstraZeneca" />)

    const img = screen.getByRole('img', { name: 'AstraZeneca' }) as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/logos/AZN.L.svg')
    expect(img.getAttribute('alt')).toBe('AstraZeneca')
  })

  it('falls back to the placeholder logo when the image fails to load', () => {
    render(<CompanyIcon ticker="AZN.L" name="AstraZeneca" />)

    const img = screen.getByRole('img', { name: 'AstraZeneca' }) as HTMLImageElement
    fireEvent.error(img)

    expect(img.getAttribute('src')).toBe('/logos/_placeholder.svg')
  })

  it('renders correctly for a different known ticker', () => {
    render(<CompanyIcon ticker="VOD.L" name="Vodafone" />)

    const img = screen.getByRole('img', { name: 'Vodafone' }) as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/logos/VOD.L.svg')
  })
})
