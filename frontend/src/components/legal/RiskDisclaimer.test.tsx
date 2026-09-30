import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { RiskDisclaimer } from './RiskDisclaimer'

// The disclaimer is the legal floor for a product that touches investment
// decisions, so the exact sentence is pinned: a future edit that softens it
// ("backtested performance", "historically proven") has to fail here first.
describe('RiskDisclaimer', () => {
  it('states the three claims that make a simulated result honest', () => {
    render(
      <MemoryRouter>
        <RiskDisclaimer />
      </MemoryRouter>,
    )

    expect(screen.getByText(/Simulated results on historical data/i)).toBeInTheDocument()
    expect(screen.getByText(/including fees and slippage/i)).toBeInTheDocument()
    expect(
      screen.getByText(/Past performance does not predict future results/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/This is not investment advice/i)).toBeInTheDocument()
  })

  it('links to the full /risk page', () => {
    render(
      <MemoryRouter>
        <RiskDisclaimer />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: /risk disclaimer/i })).toHaveAttribute('href', '/risk')
  })

  it('is exposed as a note to assistive tech, not decoration', () => {
    render(
      <MemoryRouter>
        <RiskDisclaimer />
      </MemoryRouter>,
    )

    expect(screen.getByRole('note', { name: /risk disclaimer/i })).toBeInTheDocument()
  })
})
