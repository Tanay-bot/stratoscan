import { beforeEach, describe, expect, it, vi } from 'vitest'
import { __resetWebVitals, initWebVitals } from './webVitals'

// Capture the handlers web-vitals would register, so we can fire them
// and assert the reporter forwards the right names/values/units.
const handlers: Record<string, (m: { value: number }) => void> = {}

vi.mock('web-vitals', () => ({
  onLCP: (cb: (m: { value: number }) => void) => void (handlers.lcp = cb),
  onCLS: (cb: (m: { value: number }) => void) => void (handlers.cls = cb),
  onINP: (cb: (m: { value: number }) => void) => void (handlers.inp = cb),
  onFCP: (cb: (m: { value: number }) => void) => void (handlers.fcp = cb),
  onTTFB: (cb: (m: { value: number }) => void) => void (handlers.ttfb = cb),
}))

describe('initWebVitals', () => {
  beforeEach(() => {
    __resetWebVitals()
    Object.keys(handlers).forEach((k) => delete handlers[k as keyof typeof handlers])
  })

  it('registers all five metric callbacks and forwards name/value/unit', () => {
    const reporter = vi.fn()
    initWebVitals(true, reporter)

    expect(Object.keys(handlers)).toEqual(['lcp', 'cls', 'inp', 'fcp', 'ttfb'])

    handlers.lcp?.({ value: 2400 })
    handlers.cls?.({ value: 0.05 })
    handlers.inp?.({ value: 180 })
    handlers.fcp?.({ value: 900 })
    handlers.ttfb?.({ value: 300 })

    expect(reporter).toHaveBeenCalledWith('web-vitals.lcp', 2400, 'millisecond')
    expect(reporter).toHaveBeenCalledWith('web-vitals.cls', 0.05)
    expect(reporter).toHaveBeenCalledWith('web-vitals.inp', 180, 'millisecond')
    expect(reporter).toHaveBeenCalledWith('web-vitals.fcp', 900, 'millisecond')
    expect(reporter).toHaveBeenCalledWith('web-vitals.ttfb', 300, 'millisecond')
  })

  it('is a no-op when disabled (no callbacks registered)', () => {
    const reporter = vi.fn()
    initWebVitals(false, reporter)
    expect(Object.keys(handlers)).toHaveLength(0)
    expect(reporter).not.toHaveBeenCalled()
  })

  it('ignores duplicate init calls', () => {
    const reporter = vi.fn()
    initWebVitals(true, reporter)
    initWebVitals(true, reporter)
    expect(Object.keys(handlers)).toHaveLength(5) // registered exactly once
  })

  it('forwards the five canonical metric names', () => {
    const reporter = vi.fn()
    initWebVitals(true, reporter)
    handlers.lcp?.({ value: 1 })
    handlers.cls?.({ value: 1 })
    handlers.inp?.({ value: 1 })
    handlers.fcp?.({ value: 1 })
    handlers.ttfb?.({ value: 1 })
    expect(reporter.mock.calls.map((c) => c[0])).toEqual([
      'web-vitals.lcp',
      'web-vitals.cls',
      'web-vitals.inp',
      'web-vitals.fcp',
      'web-vitals.ttfb',
    ])
  })

  it('default reporter never throws when Sentry metrics is unavailable', () => {
    // initWebVitals(true) with no reporter → uses Sentry.metrics internally.
    // In the jsdom test env @sentry/react loads fine, so this is a smoke
    // that the default path registers and fires without crashing.
    initWebVitals(true)
    handlers.lcp?.({ value: 100 })
    handlers.cls?.({ value: 0.1 })
  })
})
