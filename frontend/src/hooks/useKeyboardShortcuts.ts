import { useEffect, useRef, useState } from 'react'

/* ── Search-input registry ──────────────────────────────────────────────
 * Lets Cmd/Ctrl+K focus the most relevant search box currently on screen
 * (landing hero, app topbar, screener). Higher priority wins; ties go to
 * the most recently mounted input. Priority 0 = global chrome (topbar),
 * priority 1 = the page's own primary search box.
 */
interface RegisteredInput {
  el: HTMLInputElement
  priority: number
  order: number
}

let nextOrder = 0
const registry = new Map<HTMLInputElement, RegisteredInput>()

export function registerSearchInput(el: HTMLInputElement | null, priority = 0) {
  if (!el) return
  registry.set(el, { el, priority, order: nextOrder++ })
}

export function unregisterSearchInput(el: HTMLInputElement | null) {
  if (!el) return
  registry.delete(el)
}

export function getSearchInput(): HTMLInputElement | null {
  let best: RegisteredInput | null = null
  for (const r of registry.values()) {
    if (
      !best ||
      r.priority > best.priority ||
      (r.priority === best.priority && r.order > best.order)
    ) {
      best = r
    }
  }
  return best?.el ?? null
}

/** Attach to any search <input> to make it the Cmd+K target. */
export function useRegisterSearchInput(priority = 0) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const el = ref.current
    registerSearchInput(el, priority)
    return () => unregisterSearchInput(el)
  }, [priority])
  return ref
}

/* ── Modal dismiss-stack ────────────────────────────────────────────────
 * Custom (non-Radix) overlays register a dismiss handler while open so the
 * global Esc shortcut closes the top-most modal.
 */
const dismissers = new Set<() => void>()

export function useRegisterDismisser(fn: (() => void) | null) {
  useEffect(() => {
    if (!fn) return
    dismissers.add(fn)
    return () => {
      dismissers.delete(fn)
    }
  }, [fn])
}

function dismissTop() {
  let top: (() => void) | null = null
  for (const fn of dismissers) top = fn
  top?.()
}

/* ── The hook ─────────────────────────────────────────────────────────── */
export function useKeyboardShortcuts() {
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      const target = e.target as HTMLElement | null
      const tag = target?.tagName
      const inInput =
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        target?.isContentEditable === true

      // Any open modal owns the keyboard (Radix traps focus + handles its own
      // Esc). Defer to it so Cmd+K can't yank focus out of a dialog's trap and
      // Esc doesn't fight Radix's native close.
      const anyDialogOpen = document.querySelector('[role="dialog"]') !== null

      // Cmd/Ctrl+K → focus the primary search box
      if (mod && e.key.toLowerCase() === 'k') {
        if (anyDialogOpen) return // never steal focus from a modal
        const input = getSearchInput()
        if (!input) return // nothing to focus — don't block the browser's default
        e.preventDefault()
        input.focus()
        input.select()
        return
      }

      // Cmd/Ctrl+/ → toggle shortcut help
      if (mod && e.key === '/') {
        e.preventDefault()
        setHelpOpen((o) => !o)
        return
      }

      // Esc → close help → dismiss top modal → clear search focus
      if (e.key === 'Escape' && !mod) {
        if (helpOpen) {
          setHelpOpen(false)
          return
        }
        if (anyDialogOpen) return // a foreign Radix dialog owns Esc
        dismissTop()
        if (inInput && target instanceof HTMLElement) target.blur()
        return
      }

      // Cmd/Ctrl+Enter → submit search/backtest when focus is in an input
      if (mod && e.key === 'Enter' && inInput && target instanceof HTMLElement) {
        if (anyDialogOpen) return
        e.preventDefault()
        const form = (target as HTMLInputElement).form
        if (form) {
          form.requestSubmit()
        } else {
          // Inputs outside a form (e.g. the app topbar) handle Enter themselves.
          target.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
          )
        }
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [helpOpen])

  return { helpOpen, setHelpOpen }
}
