import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { EquityCurve } from './EquityCurve'
import { useContainerWidth } from '@/hooks/useContainerWidth'
import type { BacktestResultItem } from '@/lib/api/types'

// P2-5: recharts warns "width(0) … should be greater than 0" when
// ResponsiveContainer mounts inside a hidden/zero-width container. Backtest
// renders the chart twice (lg:hidden mobile + hidden lg:block desktop), so
// the CSS-hidden instance used to warn on every load. The fix gates chart
// mounting on a real measured width (useContainerWidth) — hidden == not
// mounted == no warning.
//
// jsdom specifics: clientWidth is always 0 and there is no ResizeObserver,
// so we stub both to simulate visible/hidden containers deterministically.

const SAMPLE: BacktestResultItem = {
  symbol: 'NVDA',
  initial_capital: 100000,
  final_value: 121000,
  total_return_pct: 21,
  sharpe_ratio: 1.3,
  max_drawdown_pct: -12,
  win_rate_pct: 54,
  total_trades: 18,
  trades: [],
  equity_curve: [
    { date: '2026-01-05', value: 100000 },
    { date: '2026-02-05', value: 106000 },
    { date: '2026-03-05', value: 121000 },
  ],
  drawdown_curve: [
    { date: '2026-01-05', value: 0 },
    { date: '2026-02-05', value: -0.06 },
    { date: '2026-03-05', value: 0 },
  ],
  warnings: [],
  benchmark_total_return_pct: 9.5,
}

/** Controllable ResizeObserver so tests can fire layout flips. */
class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []
  static width = 0
  cb: ResizeObserverCallback
  targets = new Set<Element>()
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
    FakeResizeObserver.instances.push(this)
  }
  observe(target: Element) {
    this.targets.add(target)
    // Fire once with the current simulated width, like a real observer does
    // on first observe.
    if (FakeResizeObserver.width > 0) {
      this.cb([this.entry(target)], this as unknown as ResizeObserver)
    }
  }
  unobserve(target: Element) {
    this.targets.delete(target)
  }
  disconnect() {
    this.targets.clear()
  }
  takeRecords() {
    return []
  }
  private entry(target: Element): ResizeObserverEntry {
    return {
      target,
      contentRect: { width: FakeResizeObserver.width, height: 300 },
    } as ResizeObserverEntry
  }
  /** Simulate a container changing size (e.g. a CSS breakpoint flip). */
  static emit(target: Element) {
    // Fire EVERY observer attached to the element — useContainerWidth's AND
    // recharts' internal one. Real ResizeObservers all get the same entry.
    for (const inst of FakeResizeObserver.instances) {
      if (inst.targets.has(target)) {
        inst.cb([inst.entry(target)], inst as unknown as ResizeObserver)
      }
    }
  }
}

let fakeClientWidth = 0
const originalDefineProperty = Object.defineProperty
const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

beforeEach(() => {
  fakeClientWidth = 0
  FakeResizeObserver.width = 0
  FakeResizeObserver.instances = []
  warn.mockClear()
  // jsdom's clientWidth is a hard-coded 0; override the prototype getter so
  // useContainerWidth's synchronous measure sees our simulated width.
  originalDefineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get() {
      return fakeClientWidth
    },
  })
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
  originalDefineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get() {
      return 0
    },
  })
})

describe('useContainerWidth', () => {
  function Probe() {
    const { ref, width } = useContainerWidth<HTMLDivElement>()
    return (
      <div ref={ref} data-testid="probe" data-width={width}>
        {width > 0 ? 'wide' : 'hidden'}
      </div>
    )
  }

  it('reports 0 for a hidden (zero-width) container', () => {
    fakeClientWidth = 0
    render(<Probe />)
    expect(screen.getByTestId('probe').dataset.width).toBe('0')
    expect(screen.getByText('hidden')).toBeInTheDocument()
  })

  it('reports the real width for a visible container', () => {
    fakeClientWidth = 640
    render(<Probe />)
    expect(screen.getByTestId('probe').dataset.width).toBe('640')
    expect(screen.getByText('wide')).toBeInTheDocument()
  })
})

describe('EquityCurve width gating', () => {
  it('mounts NO chart and warns NOTHING when the container is hidden', () => {
    fakeClientWidth = 0
    const { container } = render(<EquityCurve item={SAMPLE} />)
    // Toolbar is still there (it's not the chart), but no recharts surface.
    expect(screen.getByRole('button', { name: 'Equity curve' })).toBeInTheDocument()
    expect(container.querySelector('.recharts-surface')).toBeNull()
    expect(container.querySelector('.recharts-responsive-container')).toBeNull()
    // The whole point: no "width(0) … should be greater than 0" warning.
    expect(warn).not.toHaveBeenCalledWith(expect.stringContaining('width(0)'))
    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/should be greater than 0/)
  })

  it('mounts the chart once the container reports width', () => {
    fakeClientWidth = 640
    FakeResizeObserver.width = 640 // recharts' internal observer reads this
    const { container } = render(<EquityCurve item={SAMPLE} />)
    expect(container.querySelector('.recharts-surface')).not.toBeNull()
    expect(container.querySelector('.recharts-responsive-container')).not.toBeNull()
  })

  it('mounts the chart when a layout flip makes the container visible', async () => {
    // Start hidden…
    fakeClientWidth = 0
    FakeResizeObserver.width = 0
    const { container } = render(<EquityCurve item={SAMPLE} />)
    expect(container.querySelector('.recharts-surface')).toBeNull()
    // …the breakpoint flip gives the container a real size, then the
    // ResizeObserver fires. Both the hook (clientWidth) and recharts (its
    // own observer) must see the new size — same as a real browser.
    fakeClientWidth = 640
    FakeResizeObserver.width = 640
    // The wrapper div always carries the inline height style — no fallback
    // needed; a null here means the test fixture is wrong, not the app.
    const target = container.querySelector('[style]')
    expect(target).not.toBeNull()
    act(() => {
      FakeResizeObserver.emit(target!)
    })
    await waitFor(() => expect(container.querySelector('.recharts-surface')).not.toBeNull())
    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/should be greater than 0/)
  })
})
