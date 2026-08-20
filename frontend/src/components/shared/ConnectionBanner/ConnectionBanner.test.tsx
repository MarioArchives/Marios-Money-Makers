import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CONNECTION_LOST_MESSAGE, ConnectionBanner } from './ConnectionBanner'

describe('ConnectionBanner', () => {
  it('renders the single communication-loss message', () => {
    render(<ConnectionBanner />)

    expect(screen.getByText(CONNECTION_LOST_MESSAGE)).toBeInTheDocument()
    expect(CONNECTION_LOST_MESSAGE).toBe('We have lost data communication with the backend.')
  })

  it('announces politely as a status, not as an alert', () => {
    render(<ConnectionBanner />)

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
