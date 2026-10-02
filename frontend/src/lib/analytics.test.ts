import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The client module is mocked rather than imported: `track` must send the same
// headers the axios interceptor would have added, and this is the only way to
// assert that without a running backend. `DEMO_MODE` is a getter so the
// demo-mode case can flip it on the same import.
const state = vi.hoisted(() => ({ token: null as string | null, demo: false }))

vi.mock('@/lib/api/client', () => ({
  API_URL: '/api',
  get DEMO_MODE() {
    return state.demo
  },
  getToken: () => state.token,
}))

import { ANON_ID_KEY, getAnonId, track } from './analytics'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function fetchCall() {
  const mock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
  const call = mock.mock.calls[0] as [string, RequestInit]
  return { url: call[0], init: call[1], mock }
}

describe('getAnonId', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('mints an id once and returns the same one on every later call', () => {
    const first = getAnonId()

    expect(first).toMatch(UUID_RE)
    expect(getAnonId()).toBe(first)
    expect(getAnonId()).toBe(first)
    // Persisted, because the whole point is that it outlives the page view:
    // this is what joins a landing view to a signup ten minutes later.
    expect(window.localStorage.getItem(ANON_ID_KEY)).toBe(first)
  })

  it('reuses an id that is already stored instead of minting a new one', () => {
    const existing = '11111111-2222-4333-8444-555555555555'
    window.localStorage.setItem(ANON_ID_KEY, existing)

    expect(getAnonId()).toBe(existing)
  })

  it('still answers when localStorage refuses to work', () => {
    const getItem = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })

    // A private-mode browser must not crash the app over an analytics id.
    const id = getAnonId()
    expect(id).toMatch(UUID_RE)
    expect(getAnonId()).toBe(id)

    getItem.mockRestore()
  })
})

describe('track', () => {
  beforeEach(() => {
    state.token = null
    state.demo = false
    window.localStorage.clear()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the event, its props and the anonymous id, without awaiting the result', () => {
    track('query_submitted', { surface: 'screener' })

    const { url, init } = fetchCall()
    expect(url).toBe('/api/events')
    expect(init.method).toBe('POST')
    // keepalive: landing_view and result_shared usually precede a navigation,
    // and a request that dies with the document loses exactly those events.
    expect(init.keepalive).toBe(true)
    expect((init.headers as Record<string, string>)['X-Anon-Id']).toBe(getAnonId())
    expect(JSON.parse(String(init.body))).toEqual({
      name: 'query_submitted',
      props: { surface: 'screener' },
    })
  })

  it('sends an empty props object when the event carries none', () => {
    track('result_shared')

    expect(JSON.parse(String(fetchCall().init.body))).toEqual({
      name: 'result_shared',
      props: {},
    })
  })

  it('carries the Bearer token when the visitor is signed in', () => {
    state.token = 'jwt-abc'

    track('landing_view')

    expect((fetchCall().init.headers as Record<string, string>).Authorization).toBe(
      'Bearer jwt-abc',
    )
  })

  it('never throws when the request fails', async () => {
    const mock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    mock.mockRejectedValue(new Error('offline'))

    expect(() => track('landing_view')).not.toThrow()
    // The rejection is handled, not left floating: an unhandled promise
    // rejection would surface as a console error on every failed event.
    await Promise.resolve()
  })

  it('never throws when fetch itself throws synchronously', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        throw new Error('no fetch here')
      }),
    )

    expect(() => track('result_shared')).not.toThrow()
  })

  it('is a no-op in demo mode', () => {
    state.demo = true

    track('landing_view')
    track('query_submitted', { surface: 'backtest' })

    expect(globalThis.fetch).not.toHaveBeenCalled()
    // Demo mode serves synthetic data and talks to no backend; recording
    // events about it would put numbers nobody earned into the real funnel.
    expect(window.localStorage.getItem(ANON_ID_KEY)).toBeNull()
  })
})
