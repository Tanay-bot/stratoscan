import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

// The auth store is mocked as a module (not spied on): the assertion that
// matters is that the page hands the session to it, and a hoisted mock keeps
// that independent of zustand's internal state object.
const { setSessionMock } = vi.hoisted(() => ({ setSessionMock: vi.fn() }))

vi.mock('@/store/auth', () => ({
  useAuth: { getState: () => ({ setSession: setSessionMock }) },
}))
vi.mock('@/lib/api/services', () => ({ me: vi.fn() }))

import * as services from '@/lib/api/services'
import { getToken } from '@/lib/api/client'
import { GoogleComplete } from './GoogleComplete'

const USER = {
  id: 7,
  email: 'google-user@example.com',
  plan: 'free',
  email_verified: true,
  is_admin: false,
}

function LoginStub() {
  const location = useLocation()
  return <div>login stub{location.search}</div>
}

/**
 * Put the REAL window.location on /auth/google/complete (fragment included)
 * and render the page, with /app and /login as visible destinations.
 *
 * window.location rather than the router entry, because the API's redirect
 * lands on a real browser URL and the page reads window.location.hash — the
 * same thing it does in production.
 */
function at(fragment: string) {
  window.history.replaceState(null, '', `/auth/google/complete${fragment}`)
  return render(
    <MemoryRouter initialEntries={['/auth/google/complete']}>
      <Routes>
        <Route path="/auth/google/complete" element={<GoogleComplete />} />
        <Route path="/app" element={<div>dashboard stub</div>} />
        <Route path="/login" element={<LoginStub />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('GoogleComplete', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('takes the token from the hash, strips it, signs in, and lands in the app', async () => {
    vi.mocked(services.me).mockResolvedValue(USER)

    at('#token=jwt-from-google&expires_in=604800')

    // The fragment is a credential. It must be out of the address bar before
    // anything else happens (fragments never reach a server, but they do reach
    // screenshots, bookmarks and shared links)...
    expect(window.location.hash).toBe('')
    // ...and the token it carried must now be where the axios interceptor
    // reads it, because /auth/me is called next.
    expect(getToken()).toBe('jwt-from-google')

    expect(await screen.findByText('dashboard stub')).toBeInTheDocument()
    await waitFor(() =>
      expect(setSessionMock).toHaveBeenCalledWith(
        expect.objectContaining({
          access_token: 'jwt-from-google',
          expires_in: 604800,
          user: USER,
        }),
      ),
    )
  })

  it('with no token in the hash → back to login with oauth_error=failed', async () => {
    at('')

    expect(await screen.findByText(/oauth_error=failed/)).toBeInTheDocument()
    expect(services.me).not.toHaveBeenCalled()
    expect(setSessionMock).not.toHaveBeenCalled()
  })

  it('when /auth/me rejects → login, and no token is left behind', async () => {
    vi.mocked(services.me).mockRejectedValue(new Error('nope'))

    at('#token=unusable-token&expires_in=604800')

    expect(await screen.findByText(/oauth_error=failed/)).toBeInTheDocument()
    expect(setSessionMock).not.toHaveBeenCalled()
    // A stored token with no session behind it would make the next page look
    // signed in and then 401 on its first request.
    expect(getToken()).toBeNull()
  })
})
