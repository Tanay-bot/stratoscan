import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { BacktestResponse } from '@/lib/api/types'
import { DEMO_MODE, getToken } from '@/lib/api/client'
import { getPersonalData, savePersonalData } from '@/lib/api/services'

export interface SavedScreener {
  id: string
  query: string
  savedAt: string
}

interface PersonalState {
  watchlist: string[]
  savedScreeners: SavedScreener[]
  recentBacktests: (BacktestResponse & { savedAt: string })[]
  toggleWatch: (symbol: string) => void
  isWatched: (symbol: string) => boolean
  saveScreener: (query: string) => void
  removeScreener: (id: string) => void
  addRecentBacktest: (res: BacktestResponse) => void
  clearBacktests: () => void
  /** Pull the user's saved state from GET /me/personal (on login). */
  hydrateFromServer: () => Promise<void>
  /** Push the current local state to PUT /me/personal (debounced). */
  pushToServer: () => Promise<void>
}

// ── Server sync ────────────────────────────────────────────────────────────
// The store keeps localStorage (zustand persist) as the instant offline
// cache AND mirrors to the backend via /me/personal so saved data follows
// the user across devices. Push is debounced (800ms) so rapid watchlist
// toggles coalesce into one PUT. All guarded: demo mode and logged-out
// users skip the network entirely.
let pushTimer: ReturnType<typeof setTimeout> | null = null
let pushInFlight: Promise<unknown> | null = null

const canSync = () => !DEMO_MODE && typeof window !== 'undefined' && Boolean(getToken())

function schedulePush(get: () => PersonalState) {
  if (!canSync()) return
  if (pushTimer !== null) clearTimeout(pushTimer)
  pushTimer = setTimeout(() => {
    pushTimer = null
    pushInFlight = get().pushToServer()
  }, 800)
}

export const usePersonal = create<PersonalState>()(
  persist(
    (set, get) => ({
      watchlist: ['NVDA', 'META', 'LLY', 'GE'],
      savedScreeners: [
        {
          id: 'seed-1',
          query: 'momentum breakout stocks',
          savedAt: new Date(Date.now() - 86_400_000 * 2).toISOString(),
        },
        {
          id: 'seed-2',
          query: 'high-dividend energy companies',
          savedAt: new Date(Date.now() - 86_400_000 * 5).toISOString(),
        },
      ],
      recentBacktests: [],

      toggleWatch: (symbol) => {
        set((s) => ({
          watchlist: s.watchlist.includes(symbol)
            ? s.watchlist.filter((x) => x !== symbol)
            : [...s.watchlist, symbol],
        }))
        schedulePush(get)
      },

      isWatched: (symbol) => get().watchlist.includes(symbol),

      saveScreener: (query) => {
        set((s) => ({
          savedScreeners: [
            { id: `scr-${Date.now()}`, query, savedAt: new Date().toISOString() },
            ...s.savedScreeners.filter((x) => x.query !== query),
          ].slice(0, 12),
        }))
        schedulePush(get)
      },

      removeScreener: (id) => {
        set((s) => ({ savedScreeners: s.savedScreeners.filter((x) => x.id !== id) }))
        schedulePush(get)
      },

      addRecentBacktest: (res) => {
        set((s) => ({
          recentBacktests: [
            { ...res, savedAt: new Date().toISOString() },
            ...s.recentBacktests.filter((x) => x.query !== res.query),
          ].slice(0, 6),
        }))
        schedulePush(get)
      },

      clearBacktests: () => {
        set({ recentBacktests: [] })
        schedulePush(get)
      },

      hydrateFromServer: async () => {
        if (!canSync()) return
        try {
          const data = await getPersonalData()
          set({
            watchlist: data.watchlist?.length ? data.watchlist : get().watchlist,
            savedScreeners: data.saved_screeners?.length
              ? data.saved_screeners
              : get().savedScreeners,
            recentBacktests: data.recent_backtests ?? get().recentBacktests,
          })
        } catch {
          // Offline / backend hiccup — keep the local cache. The next login
          // retries, and the next mutation pushes again.
        }
      },

      pushToServer: async () => {
        if (!canSync()) return
        if (pushInFlight) {
          // Don't stack PUTs: wait for the current one, then send the latest
          // state once more so nothing is lost between rapid edits.
          await pushInFlight
          pushInFlight = null
        }
        const { watchlist, savedScreeners, recentBacktests } = get()
        // Wire the store's camelCase state to the API's snake_case contract.
        pushInFlight = savePersonalData({
          watchlist,
          saved_screeners: savedScreeners,
          recent_backtests: recentBacktests,
        })
        try {
          await pushInFlight
        } catch {
          // Fire-and-forget: a failed sync must never break the UI. The next
          // mutation (or login) will retry with the full current state.
        } finally {
          pushInFlight = null
        }
      },
    }),
    { name: 'stratoscan-personal' },
  ),
)
