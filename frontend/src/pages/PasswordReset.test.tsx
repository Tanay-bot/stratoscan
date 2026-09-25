import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const SAME_MESSAGE =
  "If an account exists for that address, we've sent a link to reset its password."

vi.mock('@/lib/api/services', () => ({
  requestPasswordReset: vi.fn(async () => ({ error: false, message: SAME_MESSAGE })),
  confirmPasswordReset: vi.fn(async () => ({ error: false, message: 'Password reset.' })),
}))

import * as services from '@/lib/api/services'
import { ForgotPassword } from './ForgotPassword'
import { ResetPassword } from './ResetPassword'

const at = (path: string, ui: React.ReactElement) =>
  render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>)

describe('ForgotPassword', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the server message verbatim, whatever the address', async () => {
    at('/forgot-password', <ForgotPassword />)
    await userEvent.type(screen.getByLabelText(/email/i), 'nobody@example.com')
    await userEvent.click(screen.getByRole('button', { name: /send reset link/i }))
    expect(await screen.findByRole('status')).toHaveTextContent(SAME_MESSAGE)
    expect(services.requestPasswordReset).toHaveBeenCalledWith('nobody@example.com')
  })

  it('rejects a malformed address without calling the API', async () => {
    at('/forgot-password', <ForgotPassword />)
    await userEvent.type(screen.getByLabelText(/email/i), 'not-an-email')
    await userEvent.click(screen.getByRole('button', { name: /send reset link/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/valid email/i)
    expect(services.requestPasswordReset).not.toHaveBeenCalled()
  })
})

describe('ResetPassword', () => {
  beforeEach(() => vi.clearAllMocks())

  it('without a token, offers a new link instead of a form', () => {
    at('/reset-password', <ResetPassword />)
    expect(screen.getByRole('alert')).toHaveTextContent(/missing its token/i)
    expect(screen.getByRole('link', { name: /send a new link/i })).toHaveAttribute(
      'href',
      '/forgot-password',
    )
  })

  it('blocks mismatched passwords client-side', async () => {
    at('/reset-password?token=abc123def456', <ResetPassword />)
    await userEvent.type(screen.getByLabelText(/^new password/i), 'long-enough-1')
    await userEvent.type(screen.getByLabelText(/confirm password/i), 'long-enough-2')
    await userEvent.click(screen.getByRole('button', { name: /set new password/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/do not match/i)
    expect(services.confirmPasswordReset).not.toHaveBeenCalled()
  })

  it('submits the token from the link and confirms success', async () => {
    at('/reset-password?token=abc123def456', <ResetPassword />)
    await userEvent.type(screen.getByLabelText(/^new password/i), 'long-enough-1')
    await userEvent.type(screen.getByLabelText(/confirm password/i), 'long-enough-1')
    await userEvent.click(screen.getByRole('button', { name: /set new password/i }))
    expect(await screen.findByRole('status')).toHaveTextContent('Password reset.')
    expect(services.confirmPasswordReset).toHaveBeenCalledWith('abc123def456', 'long-enough-1')
  })

  it('an expired or used link shows the server reason and a resend path', async () => {
    vi.mocked(services.confirmPasswordReset).mockRejectedValueOnce(
      new Error('This reset link has expired. Request a new one.'),
    )
    at('/reset-password?token=abc123def456', <ResetPassword />)
    await userEvent.type(screen.getByLabelText(/^new password/i), 'long-enough-1')
    await userEvent.type(screen.getByLabelText(/confirm password/i), 'long-enough-1')
    await userEvent.click(screen.getByRole('button', { name: /set new password/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/expired/i)
    expect(screen.getByRole('link', { name: /send a new link/i })).toBeInTheDocument()
  })
})
