import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRateLimit } from './rateLimit'

describe('rate-limit gate', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useRateLimit.getState().clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    useRateLimit.getState().clear()
  })

  it('starts idle', () => {
    const s = useRateLimit.getState()
    expect(s.pending).toBe(false)
    expect(s.retryUntil).toBeNull()
  })

  it('holds the wait window and fires the retry callback when it elapses', () => {
    const onReady = vi.fn()
    useRateLimit.getState().hold(5, onReady)
    expect(useRateLimit.getState().pending).toBe(true)
    expect(onReady).not.toHaveBeenCalled()

    vi.advanceTimersByTime(4_000)
    expect(onReady).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1_500)
    expect(onReady).toHaveBeenCalledTimes(1)
    expect(useRateLimit.getState().pending).toBe(false)
  })

  it('clear() cancels a pending retry', () => {
    const onReady = vi.fn()
    useRateLimit.getState().hold(10, onReady)
    useRateLimit.getState().clear()
    vi.advanceTimersByTime(11_000)
    expect(onReady).not.toHaveBeenCalled()
    expect(useRateLimit.getState().pending).toBe(false)
  })

  it('a later hold supersedes an earlier one (last 429 wins)', () => {
    const first = vi.fn()
    const second = vi.fn()
    useRateLimit.getState().hold(30, first)
    useRateLimit.getState().hold(2, second)
    vi.advanceTimersByTime(2_500)
    expect(second).toHaveBeenCalledTimes(1)
    expect(first).not.toHaveBeenCalled()
  })

  it('coerces sub-second waits to at least 1 second', () => {
    const onReady = vi.fn()
    useRateLimit.getState().hold(0, onReady)
    expect(useRateLimit.getState().retryUntil).not.toBeNull()
    vi.advanceTimersByTime(1_100)
    expect(onReady).toHaveBeenCalledTimes(1)
  })
})
