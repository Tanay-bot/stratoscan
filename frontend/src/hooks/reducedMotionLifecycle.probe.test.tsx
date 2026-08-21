import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { CountUp } from '@/components/landing/CountUp'

// ────────────────────────────────────────────────────────────────────────────
// BEHAVIOR PROBE (P2-6): drive the REAL CountUp through its full lifecycle.
// Unlike the suite's state tests, this traces the actual animation:
//   mount(non-reduced) → shows 0 → rAF frames → intermediate value →
//   completes at target → mid-session toggle to reduced → snaps to target.
// It also proves the reduced path needs ZERO frames.
// ────────────────────────────────────────────────────────────────────────────

/** IntersectionObserver that fires immediately so framer-motion sets inView. */
function installFiringIO() {
  class FiringIO implements IntersectionObserver {
    readonly root = null
    readonly rootMargin = ''
    readonly thresholds = []
    private cb: IntersectionObserverCallback
    constructor(cb: IntersectionObserverCallback) {
      this.cb = cb
    }
    observe = (el: Element) => {
      // Fire as a microtask so useInView flips to true after the current act.
      queueMicrotask(() =>
        this.cb([{ isIntersecting: true, target: el } as IntersectionObserverEntry], this),
      )
    }
    unobserve = () => {}
    disconnect = () => {}
    takeRecords = () => []
  }
  vi.stubGlobal('IntersectionObserver', FiringIO)
}

/** Controllable matchMedia so we can flip the preference mid-session. */
function installMatchMedia(initial: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>()
  let matches = initial
  const mql = {
    get matches() {
      return matches
    },
    media: '(prefers-reduced-motion: reduce)',
    onchange: null,
    addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
    addListener: (cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
    removeListener: (cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
    dispatchEvent: () => false,
  }
  vi.stubGlobal('matchMedia', (query: string) => ({ ...mql, media: query }) as MediaQueryList)
  return {
    set(v: boolean) {
      matches = v
      listeners.forEach((cb) => cb({ matches: v } as MediaQueryListEvent))
    },
  }
}

/** Manual rAF queue: the probe decides when frames run and what `now` is. */
type RafHandle = ReturnType<typeof installManualRaf>
function installManualRaf() {
  const queue: Array<{ id: number; cb: FrameRequestCallback }> = []
  let nextId = 1
  const raf = vi.fn((cb: FrameRequestCallback) => {
    queue.push({ id: nextId, cb })
    return nextId++
  })
  vi.stubGlobal('requestAnimationFrame', raf)
  const cancel = vi.fn((id: number) => {
    const idx = queue.findIndex((f) => f.id === id)
    if (idx >= 0) queue.splice(idx, 1)
  })
  vi.stubGlobal('cancelAnimationFrame', cancel)
  return {
    /** Run the next pending frame at the given offset on the deterministic clock. */
    frame(offsetMs: number) {
      const next = queue.shift()
      if (!next) throw new Error('no pending rAF frame — animation stopped unexpectedly')
      clock = offsetMs
      act(() => next.cb(clock))
    },
    pending: () => queue.length,
    cancelSpy: cancel,
  }
}

let mm: ReturnType<typeof installMatchMedia>
let raf: RafHandle

/** Deterministic clock: the CountUp effect captures performance.now() as its start. */
let clock: number
beforeEach(() => {
  clock = 0
  vi.spyOn(performance, 'now').mockImplementation(() => clock)
})

/** Flush microtasks so the IO callback flips framer-motion's inView state. */
async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  installFiringIO()
  mm = installMatchMedia(false)
  raf = installManualRaf()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('CountUp full lifecycle (behavior probe)', () => {
  it('animates 0 → intermediate → target across rAF frames, then stops', async () => {
    render(<CountUp target={42} duration={1400} />)
    await flush()

    // Initial render: 0, one rAF pending (animation started).
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(raf.pending()).toBe(1)

    // Frame at t=0 → eased 0 → still 0, schedules next frame.
    raf.frame(0)
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(raf.pending()).toBe(1)

    // Frame at t=100ms (t=0.071, easeOutExpo ≈ 0.39) → intermediate value 16.
    raf.frame(100)
    expect(screen.getByText('16')).toBeInTheDocument()
    expect(raf.pending()).toBe(1)

    // Frame at t=1400ms (t=1) → lands on target, and NO further frame is scheduled.
    raf.frame(1400)
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(raf.pending()).toBe(0)
  })

  it('snaps to target instantly when reduced motion turns ON mid-animation', async () => {
    render(<CountUp target={42} duration={1400} />)
    await flush()

    // Partway through the animation (value 16)...
    raf.frame(0)
    raf.frame(100)
    expect(screen.getByText('16')).toBeInTheDocument()
    expect(raf.pending()).toBe(1)

    // ...user flips the OS setting → must land on 42 immediately, no frames.
    act(() => mm.set(true))
    expect(screen.getByText('42')).toBeInTheDocument()
    // The rAF loop is cancelled (effect cleanup) and not re-scheduled.
    expect(raf.cancelSpy).toHaveBeenCalled()
    expect(raf.pending()).toBe(0)
  })

  it('requires zero frames when reduced motion is on from the start', async () => {
    mm.set(true)
    render(<CountUp target={42} duration={1400} />)
    await flush()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(raf.pending()).toBe(0)
    expect(raf.cancelSpy).not.toHaveBeenCalled()
  })
})
