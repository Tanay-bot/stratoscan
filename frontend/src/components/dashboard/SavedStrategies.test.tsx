import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const { listStrategies, deleteStrategy } = vi.hoisted(() => ({
  listStrategies: vi.fn(),
  deleteStrategy: vi.fn(),
}))
vi.mock('@/lib/api/services', () => ({ listStrategies, deleteStrategy }))

import { SavedStrategies } from './SavedStrategies'

const strat = (id: number, query: string, ret: number | null = 8.5) => ({
  id,
  query,
  symbols: ['AAPL', 'MSFT'],
  rules: { buy_rules: ['rsi < 30'], sell_rules: ['rsi > 70'] },
  saved_return_pct: ret,
  saved_at: new Date().toISOString(),
})

const renderIt = () =>
  render(
    <MemoryRouter>
      <SavedStrategies />
    </MemoryRouter>,
  )

beforeEach(() => {
  listStrategies.mockReset()
  deleteStrategy.mockReset()
})

describe('SavedStrategies', () => {
  it('explains the empty state, including that bad weeks are reported too', async () => {
    listStrategies.mockResolvedValue([])
    renderIt()
    expect(await screen.findByText('No saved strategies yet')).toBeInTheDocument()
    expect(screen.getByText(/good or bad/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Run a backtest to follow' })).toHaveAttribute(
      'href',
      '/app/backtest',
    )
  })

  it('lists what is followed and runs one again from its own query', async () => {
    listStrategies.mockResolvedValue([strat(1, 'oversold large caps & more')])
    renderIt()
    expect(await screen.findByText('“oversold large caps & more”')).toBeInTheDocument()
    expect(screen.getByText(/\+8\.5% when saved · AAPL, MSFT/)).toBeInTheDocument()
    const href = screen
      .getByRole('link', { name: 'Run oversold large caps & more again' })
      .getAttribute('href')!
    expect(href.startsWith('/app/backtest?')).toBe(true)
    // The Backtest page reads `q` and runs it on arrival.
    expect(new URLSearchParams(href.split('?')[1]).get('q')).toBe('oversold large caps & more')
  })

  it('stops following one and keeps the rest', async () => {
    listStrategies.mockResolvedValue([strat(1, 'value dip'), strat(2, 'trend', null)])
    deleteStrategy.mockResolvedValue(undefined)
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Stop following value dip' }))
    expect(deleteStrategy).toHaveBeenCalledWith(1)
    await waitFor(() => expect(screen.queryByText('“value dip”')).toBeNull())
    expect(screen.getByText('“trend”')).toBeInTheDocument()
  })

  it('keeps it listed when unfollowing fails', async () => {
    listStrategies.mockResolvedValue([strat(1, 'value dip')])
    deleteStrategy.mockRejectedValue(new Error('offline'))
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Stop following value dip' }))
    expect(screen.getByText('“value dip”')).toBeInTheDocument()
  })

  it('says what went wrong on a load failure and can retry', async () => {
    listStrategies.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce([])
    renderIt()
    expect(await screen.findByText("Couldn't load your saved strategies")).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }))
    expect(await screen.findByText('No saved strategies yet')).toBeInTheDocument()
  })
})
