import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Mock the API services BEFORE importing the auth store so login/register/me
// never hit a real network call. vi.mock hoists above imports.
vi.mock('@/lib/api/services', () => ({
  login: vi.fn(async () => ({
    access_token: 'tok.expiry',
    token_type: 'bearer',
    expires_in: 60, // short TTL so the proactive-expiry timer is testable
    user: { id: 1, email: 'a@b.co', plan: 'free', email_verified: true },
  })),
  register: vi.fn(async () => ({
    access_token: 'tok.expiry',
    token_type: 'bearer',
    expires_in: 60,
    user: { id: 1, email: 'a@b.co', plan: 'free', email_verified: true },
  })),
  me: vi.fn(async () => ({ id: 1, email: 'a@b.co', plan: 'free', email_verified: true })),
  getPersonalData: vi.fn(async () => ({
    watchlist: [],
    saved_screeners: [],
    recent_backtests: [],
  })),
  savePersonalData: vi.fn(async () => ({})),
  requiresAuth: vi.fn(() => true),
}))

// Force the real (non-demo) auth branch — the dev .env.local sets
// VITE_DEMO_MODE=true, but this test exercises the REAL expiry/multi-tab
// logic, so DEMO_MODE must be false here.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})

import { useAuth } from './auth'
import * as services from '@/lib/api/services'

describe('auth store — behavioral', () => {
  let originalLocation: Location

  beforeEach(() => {
    window.localStorage.clear()
    useAuth.setState({ user: null, token: null, bootstrapped: false })
    vi.clearAllMocks()
    originalLocation = window.location
  })

  afterEach(() => {
    vi.useRealTimers()
    // Restore the real location so later tests run in a pristine jsdom.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    })
  })

  it('login triggers a personal-data hydrate from the server', async () => {
    await useAuth.getState().login('a@b.co', 'hunter2')
    // setSession must kick off the server sync so the user's saved
    // watchlist/screeners/backtests follow them across devices.
    expect(services.getPersonalData).toHaveBeenCalled()
  })

  it('logs out in THIS tab when another tab logs out (storage event)', () => {
    window.localStorage.setItem('stratoscan_token', 'tok.other-tab')
    window.localStorage.setItem(
      'stratoscan_user',
      JSON.stringify({ id: 1, email: 'a@b.co', plan: 'free', email_verified: true }),
    )
    useAuth.setState({
      token: 'tok.other-tab',
      user: { id: 1, email: 'a@b.co', plan: 'free', email_verified: true },
      bootstrapped: true,
    })

    // Another tab removed the user key (logout). localStorage is shared
    // across tabs, so a real `storage` event fires everywhere else.
    window.dispatchEvent(
      new StorageEvent('storage', { key: 'stratoscan_user', oldValue: 'old', newValue: null }),
    )

    expect(useAuth.getState().token).toBeNull()
    expect(useAuth.getState().user).toBeNull()
    expect(window.localStorage.getItem('stratoscan_token')).toBeNull()
  })

  it('adopts the session when another tab logs in (storage event)', () => {
    // Another tab wrote token + expiry to the shared localStorage.
    window.localStorage.setItem('stratoscan_token', 'tok.new-tab')
    window.localStorage.setItem('stratoscan_token_expires_at', String(Date.now() + 3_600_000))
    const userJson = JSON.stringify({
      id: 9,
      email: 'new@tab.io',
      plan: 'free',
      email_verified: true,
    })

    window.dispatchEvent(
      new StorageEvent('storage', { key: 'stratoscan_user', oldValue: null, newValue: userJson }),
    )

    expect(useAuth.getState().token).toBe('tok.new-tab')
    expect(useAuth.getState().user?.email).toBe('new@tab.io')
    expect(useAuth.getState().bootstrapped).toBe(true)
  })
})
