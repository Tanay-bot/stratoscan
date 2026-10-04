import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'

// Exercise the real (non-demo) branches: the dev .env.local may set
// VITE_DEMO_MODE=true, which would short-circuit every call below.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})

import { api, setToken, setUnauthorizedHandler } from '@/lib/api/client'
import { backtestAsync, backtestJobStatus, claimTrialWork, shareResult } from '@/lib/api/services'
import { TRIAL_EXPIRES_KEY, TRIAL_TOKEN_KEY, ensureTrialToken, getTrialToken } from './trial'

interface Seen {
  method: string
  url: string
  headers: Record<string, unknown>
  data: unknown
}

let seen: Seen[] = []
let minted = 0
let failClaim = false
const originalAdapter = api.defaults.adapter

const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
  const headers = Object.fromEntries(
    Object.entries(config.headers.toJSON()).map(([k, v]) => [k.toLowerCase(), v]),
  )
  seen.push({
    method: (config.method ?? 'get').toUpperCase(),
    url: config.url ?? '',
    headers,
    data: config.data ? JSON.parse(config.data as string) : null,
  })
  const ok = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config })
  const url = config.url ?? ''
  if (url === '/try/session') {
    minted += 1
    return ok({ anon_token: `trial-${minted}`, expires_in: 3600, allowance: 3 })
  }
  if (url === '/try/claim' && failClaim) {
    const err = Object.assign(new Error('401'), {
      isAxiosError: true,
      config,
      response: { status: 401, data: { detail: 'Start a free trial session first' }, config },
    })
    throw err
  }
  if (url === '/try/claim') return ok({ claimed_backtests: 2, claimed_shares: 1 })
  if (url.startsWith('/results/share')) return ok({ share_id: 'x'.repeat(22), url: 'u' })
  return ok({ job_id: 'bt_1', status: 'queued', anon_remaining: 2 })
}

beforeEach(() => {
  window.localStorage.clear()
  seen = []
  minted = 0
  failClaim = false
  api.defaults.adapter = adapter
})

afterEach(() => {
  api.defaults.adapter = originalAdapter
  setUnauthorizedHandler(null)
  window.localStorage.clear()
})

describe('trial backtests (signed out)', () => {
  it('mints a trial session, then runs on /try/backtest with X-Anon-Token — never as Bearer', async () => {
    const started = await backtestAsync({ query: 'oversold tech', top_n: 5 })
    expect(started.anon_remaining).toBe(2)
    expect(seen.map((s) => `${s.method} ${s.url}`)).toEqual([
      'POST /try/session',
      'POST /try/backtest',
    ])
    expect(seen[1].headers['x-anon-token']).toBe('trial-1')
    expect(seen[1].headers.authorization).toBeUndefined()
    expect(window.localStorage.getItem(TRIAL_TOKEN_KEY)).toBe('trial-1')
  })

  it('reuses the stored trial token and polls the trial endpoint', async () => {
    await backtestAsync({ query: 'a', top_n: 5 })
    await backtestAsync({ query: 'b', top_n: 5 })
    await backtestJobStatus('bt_1')
    expect(minted).toBe(1)
    expect(seen.at(-1)).toMatchObject({ method: 'GET', url: '/try/backtest/bt_1' })
    expect(seen.at(-1)?.headers['x-anon-token']).toBe('trial-1')
  })

  it('discards an expired trial token and mints a fresh one', async () => {
    window.localStorage.setItem(TRIAL_TOKEN_KEY, 'old')
    window.localStorage.setItem(TRIAL_EXPIRES_KEY, String(Date.now() - 1000))
    expect(getTrialToken()).toBeNull()
    expect(await ensureTrialToken()).toBe('trial-1')
  })

  it('two concurrent first runs share one trial session', async () => {
    const [a, b] = await Promise.all([ensureTrialToken(), ensureTrialToken()])
    expect(a).toBe(b)
    expect(minted).toBe(1)
  })
})

describe('signed in', () => {
  it('runs on /backtest/async with the account token and never touches /try', async () => {
    setToken('acct.jwt.token')
    await backtestAsync({ query: 'x', top_n: 5 })
    await backtestJobStatus('bt_1')
    expect(seen.map((s) => `${s.method} ${s.url}`)).toEqual([
      'POST /backtest/async',
      'GET /backtest/async/bt_1',
    ])
    expect(seen[0].headers.authorization).toBe('Bearer acct.jwt.token')
  })
})

describe('sharing', () => {
  it('sends only the job id — the server builds the public numbers', async () => {
    await ensureTrialToken()
    await shareResult('bt_1')
    const call = seen.at(-1)!
    expect(call).toMatchObject({ method: 'POST', url: '/results/share' })
    expect(call.data).toEqual({ job_id: 'bt_1' })
    expect(call.headers['x-anon-token']).toBe('trial-1')
  })
})

describe('claim on sign-in', () => {
  it('does nothing without a trial token', async () => {
    setToken('acct.jwt.token')
    expect(await claimTrialWork()).toBeNull()
    expect(seen).toEqual([])
  })

  it('moves the trial work with both tokens, then forgets the trial token', async () => {
    await ensureTrialToken()
    setToken('acct.jwt.token')
    expect(await claimTrialWork()).toEqual({ claimed_backtests: 2, claimed_shares: 1 })
    const call = seen.at(-1)!
    expect(call.url).toBe('/try/claim')
    expect(call.headers.authorization).toBe('Bearer acct.jwt.token')
    expect(call.headers['x-anon-token']).toBe('trial-1')
    expect(getTrialToken()).toBeNull()
  })

  it('keeps the trial token when the claim fails — and a 401 there never logs the user out', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    await ensureTrialToken()
    setToken('acct.jwt.token')
    failClaim = true
    expect(await claimTrialWork()).toBeNull()
    expect(getTrialToken()).toBe('trial-1')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})
