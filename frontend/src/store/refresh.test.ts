import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api/services', () => ({
  refreshSession: vi.fn(async () => ({
    access_token: 'tok.renewed',
    token_type: 'bearer',
    expires_in: 604800,
  })),
  me: vi.fn(),
  requiresAuth: vi.fn(() => true),
}))
vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})

import { useAuth, REFRESH_WHEN_WITHIN_MS } from './auth'
import * as services from '@/lib/api/services'
import { TOKEN_EXPIRES_KEY, TOKEN_KEY } from '@/lib/api/client'

const HOUR = 60 * 60 * 1000
const setExpiry = (msFromNow: number) =>
  window.localStorage.setItem(TOKEN_EXPIRES_KEY, String(Date.now() + msFromNow))

describe('sliding session — refreshIfStale', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    window.localStorage.setItem(TOKEN_KEY, 'tok.original')
    useAuth.setState({ token: 'tok.original', user: null, bootstrapped: true })
  })

  it('does nothing while the token is far from its deadline', async () => {
    setExpiry(REFRESH_WHEN_WITHIN_MS + 6 * HOUR)
    await useAuth.getState().refreshIfStale()
    expect(services.refreshSession).not.toHaveBeenCalled()
    expect(useAuth.getState().token).toBe('tok.original')
  })

  it('renews inside the window and stores the new token and deadline', async () => {
    setExpiry(REFRESH_WHEN_WITHIN_MS - HOUR)
    await useAuth.getState().refreshIfStale()
    expect(services.refreshSession).toHaveBeenCalledTimes(1)
    expect(useAuth.getState().token).toBe('tok.renewed')
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe('tok.renewed')
    const exp = Number(window.localStorage.getItem(TOKEN_EXPIRES_KEY))
    expect(exp - Date.now()).toBeGreaterThan(6 * 24 * HOUR)
  })

  it('a refused refresh leaves the existing session in place', async () => {
    vi.mocked(services.refreshSession).mockRejectedValueOnce(new Error('too old'))
    setExpiry(HOUR)
    await useAuth.getState().refreshIfStale()
    expect(useAuth.getState().token).toBe('tok.original')
  })

  it('never tries to renew an already-expired token', async () => {
    setExpiry(-HOUR)
    await useAuth.getState().refreshIfStale()
    expect(services.refreshSession).not.toHaveBeenCalled()
  })

  it('concurrent triggers make one request, not several', async () => {
    setExpiry(HOUR)
    await Promise.all([
      useAuth.getState().refreshIfStale(),
      useAuth.getState().refreshIfStale(),
      useAuth.getState().refreshIfStale(),
    ])
    expect(services.refreshSession).toHaveBeenCalledTimes(1)
  })
})
