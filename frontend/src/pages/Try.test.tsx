import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

const { state } = vi.hoisted(() => ({ state: { token: null as string | null } }))
vi.mock('@/store/auth', () => ({
  useAuth: (sel: (s: { token: string | null }) => unknown) => sel(state),
}))
// The workspace itself is covered elsewhere; here only the gate matters.
vi.mock('./Backtest', () => ({ Backtest: () => <div>backtest workspace</div> }))

import { Try } from './Try'

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname + loc.search}</div>
}

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/try" element={<Try />} />
        <Route path="/app/backtest" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )

describe('/try', () => {
  it('serves the backtester to a signed-out visitor, with signup offered but not required', () => {
    state.token = null
    at('/try?q=oversold%20tech')
    expect(screen.getByText('backtest workspace')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign up free' })).toHaveAttribute(
      'href',
      '/signup?next=%2Fapp%2Fbacktest',
    )
  })

  it('sends a signed-in visitor to the full app, keeping the query', () => {
    state.token = 'acct.jwt'
    at('/try?q=oversold%20tech')
    expect(screen.getByTestId('where')).toHaveTextContent('/app/backtest?q=oversold%20tech')
  })
})
