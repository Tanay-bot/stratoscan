import { useEffect, useState } from 'react'
import { create } from 'zustand'

/**
 * Shared "you hit a 429" gate.
 *
 * When the backend rate-limits a request it answers 429 with a
 * `Retry-After: <seconds>` header. The page that caught the 429 calls
 * `hold()` with that wait and a callback that re-issues the exact same
 * request; when the countdown reaches zero the callback fires (auto-retry).
 * While pending, every submit surface reads `pending` / `waitSeconds` from
 * `useRateLimitCountdown()` and disables itself, showing a live countdown.
 *
 * The gate is module-global so the screener and backtester share one
 * countdown, and a later 429 supersedes an earlier one (last wait wins).
 */

interface RateLimitState {
  pending: boolean
  retryUntil: number | null
  onReady: (() => void) | null
  hold: (seconds: number, onReady: () => void) => void
  clear: () => void
}

let timer: ReturnType<typeof setTimeout> | null = null

export const useRateLimit = create<RateLimitState>((set, get) => ({
  pending: false,
  retryUntil: null,
  onReady: null,

  hold: (seconds, onReady) => {
    if (timer) clearTimeout(timer)
    const ms = Math.max(1, Math.round(seconds)) * 1000
    set({ pending: true, retryUntil: Date.now() + ms, onReady })
    timer = setTimeout(() => {
      timer = null
      const cb = get().onReady
      get().clear()
      cb?.()
    }, ms)
  },

  clear: () => {
    if (timer) clearTimeout(timer)
    timer = null
    set({ pending: false, retryUntil: null, onReady: null })
  },
}))

/** Live countdown for submit surfaces: `{ pending, waitSeconds }`. */
export function useRateLimitCountdown(): { pending: boolean; waitSeconds: number } {
  const pending = useRateLimit((s) => s.pending)
  const retryUntil = useRateLimit((s) => s.retryUntil)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!pending) return
    setNow(Date.now())
    const tick = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(tick)
  }, [pending])

  const waitSeconds = retryUntil ? Math.max(0, Math.ceil((retryUntil - now) / 1000)) : 0
  return { pending, waitSeconds }
}
