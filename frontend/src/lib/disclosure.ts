import { useCallback, useEffect, useState } from 'react'

/**
 * Progressive disclosure state (Day 9).
 *
 * Every layer past the result is collapsed by default; whatever a person
 * opens stays open on their next visit, per user (signed-out visitors share
 * one "guest" slot per browser). localStorage only — a convenience, never
 * state anything depends on, so every read and write tolerates failure.
 */

export type Layer = 'rules' | 'adjust' | 'code'
export const LAYERS: readonly Layer[] = ['rules', 'adjust', 'code']

export const disclosureKey = (userKey: string) => `stratoscan_layers:${userKey}`

export type LayerState = Record<Layer, boolean>

export const CLOSED: LayerState = { rules: false, adjust: false, code: false }

export function readLayers(userKey: string): LayerState {
  try {
    const raw = window.localStorage.getItem(disclosureKey(userKey))
    if (!raw) return { ...CLOSED }
    const parsed = JSON.parse(raw) as Partial<Record<string, unknown>>
    const out = { ...CLOSED }
    for (const l of LAYERS) out[l] = parsed[l] === true
    return out
  } catch {
    return { ...CLOSED }
  }
}

export function writeLayers(userKey: string, state: LayerState): void {
  try {
    window.localStorage.setItem(disclosureKey(userKey), JSON.stringify(state))
  } catch {
    // private mode / quota — the panels still work, they just won't be remembered
  }
}

/** `[isOpen, toggle, setOpen]` for each layer, persisted per user. */
export function useLayers(userKey: string) {
  const [state, setState] = useState<LayerState>(() => readLayers(userKey))

  // A sign-in or sign-out switches to that person's remembered layout.
  useEffect(() => setState(readLayers(userKey)), [userKey])

  const set = useCallback(
    (layer: Layer, open: boolean) => {
      setState((prev) => {
        const next = { ...prev, [layer]: open }
        writeLayers(userKey, next)
        return next
      })
    },
    [userKey],
  )
  const toggle = useCallback(
    (layer: Layer) => {
      setState((prev) => {
        const next = { ...prev, [layer]: !prev[layer] }
        writeLayers(userKey, next)
        return next
      })
    },
    [userKey],
  )
  return { layers: state, toggle, set }
}
