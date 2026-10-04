import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

const { state, track } = vi.hoisted(() => ({
  state: { token: null as string | null },
  track: vi.fn(),
}))
vi.mock('@/store/auth', () => ({
  useAuth: (sel: (s: { token: string | null; user: null }) => unknown) =>
    sel({ token: state.token, user: null }),
}))
vi.mock('@/lib/analytics', () => ({ track }))
vi.mock('@/components/landing/LandingNav', () => ({ LandingNav: () => null }))
vi.mock('@/components/landing/Footer', () => ({ Footer: () => null }))

import { Strategies } from './Strategies'
import { TEMPLATES } from '@/lib/templates'

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname + loc.search}</div>
}

const renderAt = () =>
  render(
    <MemoryRouter initialEntries={['/strategies']}>
      <Routes>
        <Route path="/strategies" element={<Strategies />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )

describe('/strategies', () => {
  it('lists every template with its query and exactly what is simulated', () => {
    state.token = null
    renderAt()
    expect(screen.getAllByRole('button', { name: /Run this backtest/ })).toHaveLength(
      TEMPLATES.length,
    )
    for (const t of TEMPLATES) {
      expect(screen.getByRole('heading', { name: t.title, level: 2 })).toBeInTheDocument()
    }
    expect(screen.getAllByText('Simulated as:')).toHaveLength(TEMPLATES.length)
  })

  it('runs a template as a free trial when signed out, tracking only its id', async () => {
    state.token = null
    renderAt()
    await userEvent.click(screen.getAllByRole('button', { name: /Run this backtest/ })[0])
    expect(screen.getByTestId('where')).toHaveTextContent(
      `/try?q=${encodeURIComponent(TEMPLATES[0].query)}`,
    )
    expect(track).toHaveBeenCalledWith('template_used', { template: TEMPLATES[0].id })
  })

  it('runs it in the full app when signed in', async () => {
    state.token = 'acct.jwt'
    renderAt()
    await userEvent.click(screen.getAllByRole('button', { name: /Run this backtest/ })[1])
    expect(screen.getByTestId('where')).toHaveTextContent(
      `/app/backtest?q=${encodeURIComponent(TEMPLATES[1].query)}`,
    )
  })
})
