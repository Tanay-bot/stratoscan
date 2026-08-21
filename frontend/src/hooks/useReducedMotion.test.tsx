import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { useReducedMotion } from './useReducedMotion'
import { CountUp } from '@/components/landing/CountUp'
import { TickerTape } from '@/components/charts/TickerTape'

// P2-6: JS-driven animations must respect prefers-reduced-motion. Pure CSS
// animations are already frozen by the global @media block in index.css, but
// CountUp's rAF count-up and TickerTape's marquee loop are JS/CSS-class
// driven and need explicit gating. These tests stub matchMedia to simulate
// the user's motion preference.

/** Controllable matchMedia so tests can flip the preference (even live). */
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
    /** Flip the preference and fire change listeners, like an OS toggle. */
    set(v: boolean) {
      matches = v
      const event = { matches: v } as MediaQueryListEvent
      listeners.forEach((cb) => cb(event))
    },
  }
}

// TickerTape renders nothing unless DEMO_MODE — force it on for this test.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...mod, DEMO_MODE: true }
})

function Probe() {
  const reduced = useReducedMotion()
  return <span data-testid="reduced">{String(reduced)}</span>
}

beforeEach(() => {
  installMatchMedia(false)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useReducedMotion', () => {
  it('returns false when the user has no motion preference', () => {
    installMatchMedia(false)
    render(<Probe />)
    expect(screen.getByTestId('reduced').textContent).toBe('false')
  })

  it('returns true when prefers-reduced-motion is set', () => {
    installMatchMedia(true)
    render(<Probe />)
    expect(screen.getByTestId('reduced').textContent).toBe('true')
  })

  it('reacts live when the preference changes mid-session', () => {
    const mm = installMatchMedia(false)
    render(<Probe />)
    expect(screen.getByTestId('reduced').textContent).toBe('false')
    act(() => mm.set(true))
    expect(screen.getByTestId('reduced').textContent).toBe('true')
    act(() => mm.set(false))
    expect(screen.getByTestId('reduced').textContent).toBe('false')
  })
})

describe('CountUp reduced motion', () => {
  it('lands on the final value instantly when reduced motion is on', () => {
    installMatchMedia(true)
    render(<CountUp target={42} />)
    // No rAF count-up — the number is content and must be readable as-is.
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('starts at 0 (animates) when motion is allowed', () => {
    installMatchMedia(false)
    render(<CountUp target={42} />)
    // In jsdom the IntersectionObserver never fires, so the count-up never
    // advances — which is exactly the "gated" start state.
    expect(screen.getByText('0')).toBeInTheDocument()
  })
})

describe('TickerTape reduced motion', () => {
  it('renders a single static row without the marquee loop when reduced', () => {
    installMatchMedia(true)
    const { container } = render(<TickerTape />)
    // One row (no doubled seamless-loop copy), and no animate-ticker class.
    expect(container.querySelectorAll('.animate-ticker').length).toBe(0)
    // Row divs carry pr-8; exactly one in reduced mode.
    expect(container.querySelectorAll('[class*="pr-8"]').length).toBe(1)
  })

  it('keeps the doubled marquee loop when motion is allowed', () => {
    installMatchMedia(false)
    const { container } = render(<TickerTape />)
    expect(container.querySelectorAll('.animate-ticker').length).toBe(1)
    expect(container.querySelectorAll('[class*="pr-8"]').length).toBe(2)
  })
})
