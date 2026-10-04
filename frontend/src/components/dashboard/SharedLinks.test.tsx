import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const { listShares, revokeShare } = vi.hoisted(() => ({
  listShares: vi.fn(),
  revokeShare: vi.fn(),
}))
vi.mock('@/lib/api/services', () => ({ listShares, revokeShare }))

import { SharedLinks } from './SharedLinks'

const share = (id: string, query: string, ret: number | null = 12.3) => ({
  share_id: id,
  url: `https://api.example/r/${id}`,
  query,
  created_at: new Date().toISOString(),
  headline: ret === null ? null : { total_return_pct: ret, benchmark_total_return_pct: 100 },
})

const renderIt = () =>
  render(
    <MemoryRouter>
      <SharedLinks />
    </MemoryRouter>,
  )

beforeEach(() => {
  listShares.mockReset()
  revokeShare.mockReset()
})

describe('SharedLinks', () => {
  it('explains the empty state and says nothing is public until you share', async () => {
    listShares.mockResolvedValue([])
    renderIt()
    expect(await screen.findByText('Nothing shared yet')).toBeInTheDocument()
    expect(screen.getByText(/Nothing is public until you do/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Run a backtest to share' })).toHaveAttribute(
      'href',
      '/app/backtest',
    )
  })

  it('lists live links and turns one off for everyone', async () => {
    listShares.mockResolvedValue([
      share('a'.repeat(22), 'oversold tech'),
      share('b'.repeat(22), 'value dip', null),
    ])
    revokeShare.mockResolvedValue(undefined)
    renderIt()
    expect(await screen.findByText('“oversold tech”')).toBeInTheDocument()
    expect(screen.getByText(/\+12\.3%/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Stop sharing oversold tech' }))
    expect(revokeShare).toHaveBeenCalledWith('a'.repeat(22))
    await waitFor(() => expect(screen.queryByText('“oversold tech”')).toBeNull())
    expect(screen.getByText('“value dip”')).toBeInTheDocument()
  })

  it('keeps a link listed when revoking fails', async () => {
    listShares.mockResolvedValue([share('a'.repeat(22), 'oversold tech')])
    revokeShare.mockRejectedValue(new Error('offline'))
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Stop sharing oversold tech' }))
    expect(screen.getByText('“oversold tech”')).toBeInTheDocument()
  })

  it('says what went wrong on a load failure and can retry', async () => {
    listShares.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce([])
    renderIt()
    expect(await screen.findByText("Couldn't load your share links")).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }))
    expect(await screen.findByText('Nothing shared yet')).toBeInTheDocument()
  })
})
