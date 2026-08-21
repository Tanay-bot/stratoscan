import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

/**
 * Reactive `prefers-reduced-motion` hook.
 *
 * Unlike a one-time module-level `const REDUCED = matchMedia(...).matches`,
 * this subscribes to `change` events, so toggling the OS/browser setting
 * while the page is open re-renders gated components live. It also survives
 * hydration/SSR safely (returns false when matchMedia is unavailable).
 *
 * Use it for JS-driven animation (rAF loops, timers, state-based motion).
 * Pure CSS animations are already frozen globally by the
 * `@media (prefers-reduced-motion: reduce)` block in index.css.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
    return window.matchMedia(QUERY).matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(QUERY)
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mql.addEventListener('change', onChange)
    setReduced(mql.matches) // re-sync in case it changed between render and effect
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return reduced
}
