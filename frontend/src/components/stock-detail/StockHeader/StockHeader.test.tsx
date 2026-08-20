import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StockHeader } from './StockHeader'

// CompanyIcon is exercised by its own unit tests; stub it here so
// StockHeader's tests fail only because of StockHeader's own missing
// implementation, not because of CompanyIcon's.
vi.mock('../../shared/CompanyIcon/CompanyIcon', () => ({
  CompanyIcon: ({ name }: { ticker: string; name: string; size?: number }) => (
    // eslint-disable-next-line jsx-a11y/alt-text
    <img role="img" alt={name} data-testid="mock-company-icon" src="mocked-icon.svg" />
  ),
}))

describe('StockHeader', () => {
  it('renders the company name', () => {
    render(<StockHeader ticker="AZN.L" name="AstraZeneca" sector="Pharma" />)

    expect(screen.getByText('AstraZeneca')).toBeInTheDocument()
  })

  it('renders the sector', () => {
    render(<StockHeader ticker="AZN.L" name="AstraZeneca" sector="Pharma" />)

    expect(screen.getByText('Pharma')).toBeInTheDocument()
  })

  it('renders the CompanyIcon for the given ticker/name', () => {
    render(<StockHeader ticker="AZN.L" name="AstraZeneca" sector="Pharma" />)

    expect(screen.getByTestId('mock-company-icon')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'AstraZeneca' })).toBeInTheDocument()
  })

  it('renders correctly for a different ticker/name/sector combination', () => {
    render(<StockHeader ticker="VOD.L" name="Vodafone" sector="Telecom" />)

    expect(screen.getByText('Vodafone')).toBeInTheDocument()
    expect(screen.getByText('Telecom')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Vodafone' })).toBeInTheDocument()
  })
})
