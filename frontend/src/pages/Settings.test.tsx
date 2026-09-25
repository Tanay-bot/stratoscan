import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})
vi.mock('@/lib/api/services', () => ({
  deleteAccount: vi.fn(async () => ({ error: false, message: 'Deleted.' })),
  changePassword: vi.fn(),
  logoutEverywhere: vi.fn(async () => ({ error: false, message: 'ok' })),
}))

import * as services from '@/lib/api/services'
import { useAuth } from '@/store/auth'
import { Settings } from './Settings'

const renderSettings = () =>
  render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  )

describe('Settings — account deletion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuth.setState({
      token: 'tok',
      user: { id: 1, email: 'me@x.test', plan: 'free', email_verified: true, is_admin: false },
    })
  })

  it('stays disabled until the password is entered and DELETE is typed', async () => {
    renderSettings()
    await userEvent.click(screen.getByRole('button', { name: /delete my account/i }))
    const submit = screen.getByRole('button', { name: /permanently delete/i })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/your password/i), 'pw-123456')
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/type delete/i), 'delete')
    expect(submit).toBeDisabled() // case-sensitive on purpose
    await userEvent.clear(screen.getByLabelText(/type delete/i))
    await userEvent.type(screen.getByLabelText(/type delete/i), 'DELETE')
    expect(submit).toBeEnabled()
  })

  it('deletes with the password, then signs this browser out', async () => {
    renderSettings()
    await userEvent.click(screen.getByRole('button', { name: /delete my account/i }))
    await userEvent.type(screen.getByLabelText(/your password/i), 'pw-123456')
    await userEvent.type(screen.getByLabelText(/type delete/i), 'DELETE')
    await userEvent.click(screen.getByRole('button', { name: /permanently delete/i }))
    expect(services.deleteAccount).toHaveBeenCalledWith('pw-123456')
    expect(useAuth.getState().token).toBeNull()
  })

  it('a wrong password shows the reason and keeps the session', async () => {
    vi.mocked(services.deleteAccount).mockRejectedValueOnce(
      new Error('Your password is incorrect.'),
    )
    renderSettings()
    await userEvent.click(screen.getByRole('button', { name: /delete my account/i }))
    await userEvent.type(screen.getByLabelText(/your password/i), 'wrong')
    await userEvent.type(screen.getByLabelText(/type delete/i), 'DELETE')
    await userEvent.click(screen.getByRole('button', { name: /permanently delete/i }))
    expect(await screen.findByText(/password is incorrect/i)).toBeInTheDocument()
    expect(useAuth.getState().token).toBe('tok')
  })
})
