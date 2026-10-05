import { useEffect } from 'react'
import { create } from 'zustand'
import { getEntitlements } from '@/lib/api/services'
import type { Entitlements } from '@/lib/api/types'
import { useAuth } from '@/store/auth'

/**
 * The signed-in account's plan, limits and usage (Day 12).
 *
 * Display only: the server enforces every gate on every request, so a stale
 * value here can at worst show a button the API then refuses (with a clear
 * message). Refreshed whenever the account changes and after anything that
 * spends a limit (`refresh()`).
 */
interface EntitlementsState {
  data: Entitlements | null
  forToken: string | null
  load: (token: string) => Promise<void>
  refresh: () => Promise<void>
  clear: () => void
}

export const useEntitlementsStore = create<EntitlementsState>((set, get) => ({
  data: null,
  forToken: null,
  load: async (token) => {
    if (get().forToken === token) return
    set({ forToken: token })
    try {
      const data = await getEntitlements()
      if (get().forToken === token) set({ data })
    } catch {
      // Unknown plan → show nothing gated; the API still decides.
      if (get().forToken === token) set({ data: null })
    }
  },
  refresh: async () => {
    const token = get().forToken
    if (!token) return
    try {
      const data = await getEntitlements()
      if (get().forToken === token) set({ data })
    } catch {
      /* keep the last known value */
    }
  },
  clear: () => set({ data: null, forToken: null }),
}))

/** The account's entitlements (null while loading, signed out or unknown). */
export function useEntitlements(): Entitlements | null {
  const token = useAuth((s) => s.token)
  const { data, load, clear } = useEntitlementsStore()
  useEffect(() => {
    if (token) void load(token)
    else clear()
  }, [token, load, clear])
  return token ? data : null
}
