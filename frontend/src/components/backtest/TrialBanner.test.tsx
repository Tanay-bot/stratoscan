import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { TRIAL_ALLOWANCE, TrialBanner } from './TrialBanner'

const renderBanner = (left: number | null, exhausted = false) =>
  render(
    <MemoryRouter>
      <TrialBanner left={left} exhausted={exhausted} />
    </MemoryRouter>,
  )

describe('TrialBanner', () => {
  it('shows the full allowance before the first run, and never blocks', () => {
    renderBanner(null)
    expect(screen.getByRole('status')).toHaveTextContent(`${TRIAL_ALLOWANCE} backtests left`)
    expect(screen.queryByText(/used your/)).toBeNull()
  })

  it('counts down with correct pluralization', () => {
    renderBanner(1)
    expect(screen.getByRole('status')).toHaveTextContent('1 backtest left')
  })

  it('turns into the signup ask once the server refuses a run — and promises nothing is lost', () => {
    renderBanner(0, true)
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent(`used your ${TRIAL_ALLOWANCE} free backtests`)
    expect(status).toHaveTextContent('come with you')
    expect(screen.getByRole('link', { name: 'Create free account' })).toHaveAttribute(
      'href',
      '/signup?next=%2Fapp%2Fbacktest',
    )
    expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute(
      'href',
      '/login?next=%2Fapp%2Fbacktest',
    )
  })
})
