import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { CLOSED, disclosureKey, readLayers, useLayers } from './disclosure'

beforeEach(() => window.localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('progressive disclosure state', () => {
  it('starts with every layer collapsed', () => {
    const { result } = renderHook(() => useLayers('guest'))
    expect(result.current.layers).toEqual(CLOSED)
  })

  it('remembers what a person opened across a reload', () => {
    const first = renderHook(() => useLayers('user:7'))
    act(() => first.result.current.toggle('code'))
    act(() => first.result.current.toggle('rules'))
    first.unmount()
    // A fresh mount reads storage again — the same as a page reload.
    const again = renderHook(() => useLayers('user:7'))
    expect(again.result.current.layers).toEqual({ rules: true, adjust: false, code: true })
  })

  it('keeps each user separate, and switches when the user changes', () => {
    const { result, rerender } = renderHook(({ k }) => useLayers(k), {
      initialProps: { k: 'user:1' },
    })
    act(() => result.current.set('adjust', true))
    rerender({ k: 'user:2' })
    expect(result.current.layers).toEqual(CLOSED)
    rerender({ k: 'user:1' })
    expect(result.current.layers.adjust).toBe(true)
  })

  it('treats corrupt or foreign values as closed', () => {
    window.localStorage.setItem(disclosureKey('guest'), '{not json')
    expect(readLayers('guest')).toEqual(CLOSED)
    window.localStorage.setItem(disclosureKey('guest'), JSON.stringify({ code: 'yes', rules: 1 }))
    expect(readLayers('guest')).toEqual(CLOSED)
  })

  it('still works when storage throws (private mode)', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const { result } = renderHook(() => useLayers('guest'))
    act(() => result.current.toggle('rules'))
    expect(result.current.layers.rules).toBe(true)
  })
})
