import { beforeEach, describe, expect, it, vi } from 'vitest'

// Mock the API services BEFORE importing the auth store so login/register/
// me never hit a real network call. vi.mock hoists above imports.
vi.mock('@/lib/api/services', () => ({
  login: vi.fn(async () => ({
    access_token: 'tok.test',
    token_type: 'bearer',
    expires_in: 604800,
    user: { id: 1, email: 'a@b.co', plan: 'free', email_verified: true },
  })),
  register: vi.fn(async () => ({
    access_token: 'tok.test',
    token_type: 'bearer',
    expires_in: 604800,
    user: { id: 1, email: 'a@b.co', plan: 'free', email_verified: true },
  })),
  me: vi.fn(async () => ({ id: 1, email: 'a@b.co', plan: 'free', email_verified: true })),
  requiresAuth: vi.fn(() => true),
}))

// Force the real (non-demo) auth branch: the dev .env.local sets
// VITE_DEMO_MODE=true so the app demos without a backend — but this test
// exercises the REAL revalidation/expiry logic, so the client's DEMO_MODE
// flag must be false here. Keep everything else from the real module.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})

import { useAuth } from './auth'
import * as services from '@/lib/api/services'

describe('auth store', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useAuth.setState({
      user: null,
      token: null,
      bootstrapped: false,
    })
    vi.clearAllMocks()
  })

  it('login persists the session and user', async () => {
    const user = await useAuth.getState().login('a@b.co', 'hunter2')
    expect(user.email).toBe('a@b.co')
    expect(useAuth.getState().token).toBe('tok.test')
    expect(useAuth.getState().bootstrapped).toBe(true)
    expect(window.localStorage.getItem('stratoscan_token')).toBe('tok.test')
    expect(window.localStorage.getItem('stratoscan_user')).toContain('a@b.co')
  })

  it('logout clears everything', async () => {
    await useAuth.getState().login('a@b.co', 'hunter2')
    useAuth.getState().logout()
    expect(useAuth.getState().token).toBeNull()
    expect(useAuth.getState().user).toBeNull()
    expect(window.localStorage.getItem('stratoscan_token')).toBeNull()
  })

  it('bootstrap revalidates the session via /auth/me', async () => {
    window.localStorage.setItem('stratoscan_token', 'tok.test')
    window.localStorage.setItem(
      'stratoscan_user',
      JSON.stringify({ id: 1, email: 'a@b.co', plan: 'free' }),
    )
    window.localStorage.setItem('stratoscan_token_expires_at', String(Date.now() + 60_000))
    useAuth.setState({ token: 'tok.test', bootstrapped: false })

    await useAuth.getState().bootstrap()

    expect(services.me).toHaveBeenCalled()
    expect(useAuth.getState().bootstrapped).toBe(true)
    expect(useAuth.getState().user?.email).toBe('a@b.co')
  })

  it('bootstrap logs out when the persisted expiry has passed', async () => {
    window.localStorage.setItem('stratoscan_token', 'tok.test')
    window.localStorage.setItem(
      'stratoscan_user',
      JSON.stringify({ id: 1, email: 'a@b.co', plan: 'free' }),
    )
    // Expired 5 minutes ago — the JWT is already invalid server-side, so the
    // doomed /auth/me call (it would 401) is skipped and the session clears.
    window.localStorage.setItem('stratoscan_token_expires_at', String(Date.now() - 5 * 60_000))
    useAuth.setState({ token: 'tok.test', bootstrapped: false })

    await useAuth.getState().bootstrap()

    expect(services.me).not.toHaveBeenCalled()
    expect(useAuth.getState().token).toBeNull()
    expect(useAuth.getState().user).toBeNull()
  })

  it('adopts a token rotated in another tab via the storage event', () => {
    useAuth.setState({
      token: 'tok.a',
      user: { id: 1, email: 'a@b.co', plan: 'free', email_verified: true },
      bootstrapped: true,
    })
    window.localStorage.setItem('stratoscan_token', 'tok.b')
    window.localStorage.setItem('stratoscan_token_expires_at', String(Date.now() + 3_600_000))

    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'stratoscan_token',
        newValue: 'tok.b',
        oldValue: 'tok.a',
      }),
    )

    expect(useAuth.getState().token).toBe('tok.b')
  })
})
