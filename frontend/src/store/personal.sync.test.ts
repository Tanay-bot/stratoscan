import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api/services', () => ({
  getPersonalData: vi.fn(async () => ({
    watchlist: [],
    saved_screeners: [],
    recent_backtests: [],
  })),
  savePersonalData: vi.fn(async () => ({})),
  requiresAuth: vi.fn(() => true),
}))

// The dev .env.local enables demo mode; these tests exercise the REAL
// server-sync path, so DEMO_MODE must be false.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})

import { usePersonal } from './personal'
import { setToken } from '@/lib/api/client'
import * as services from '@/lib/api/services'

function reset() {
  usePersonal.setState({ watchlist: [], savedScreeners: [], recentBacktests: [] })
}

describe('personal store — server sync (behavioral)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    reset()
    setToken('tok.sync')
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('hydrateFromServer replaces local state when the server has data', async () => {
    vi.mocked(services.getPersonalData).mockResolvedValueOnce({
      watchlist: ['NVDA', 'AAPL'],
      saved_screeners: [{ id: 'srv-1', query: 'server query', savedAt: '2026-01-01T00:00:00Z' }],
      recent_backtests: [],
    })

    await usePersonal.getState().hydrateFromServer()

    expect(usePersonal.getState().watchlist).toEqual(['NVDA', 'AAPL'])
    expect(usePersonal.getState().savedScreeners[0].query).toBe('server query')
  })

  it('hydrateFromServer keeps local state when the server is empty (offline cache wins)', async () => {
    usePersonal.setState({ watchlist: ['LOCAL'], savedScreeners: [], recentBacktests: [] })
    vi.mocked(services.getPersonalData).mockResolvedValueOnce({
      watchlist: [],
      saved_screeners: [],
      recent_backtests: [],
    })

    await usePersonal.getState().hydrateFromServer()

    expect(usePersonal.getState().watchlist).toEqual(['LOCAL'])
  })

  it('pushToServer sends camelCase store state as the snake_case API contract', async () => {
    usePersonal.setState({
      watchlist: ['NVDA'],
      savedScreeners: [{ id: 's1', query: 'q', savedAt: '2026-01-01T00:00:00Z' }],
      recentBacktests: [],
    })

    await usePersonal.getState().pushToServer()

    expect(services.savePersonalData).toHaveBeenCalledWith({
      watchlist: ['NVDA'],
      saved_screeners: [{ id: 's1', query: 'q', savedAt: '2026-01-01T00:00:00Z' }],
      recent_backtests: [],
    })
  })

  it('debounces rapid toggles into a single PUT', async () => {
    vi.useFakeTimers()
    usePersonal.getState().toggleWatch('NVDA')
    usePersonal.getState().toggleWatch('META')
    usePersonal.getState().toggleWatch('GE')
    usePersonal.getState().toggleWatch('NVDA') // remove again

    expect(services.savePersonalData).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(900)

    expect(services.savePersonalData).toHaveBeenCalledTimes(1)
    expect(services.savePersonalData).toHaveBeenCalledWith(
      expect.objectContaining({ watchlist: ['META', 'GE'] }),
    )
  })
})
