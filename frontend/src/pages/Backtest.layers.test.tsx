import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { BacktestResponse } from '@/lib/api/types'

// Demo mode is forced on for this suite so the page takes its synchronous
// `backtest()` path; every other export of the client stays real (the spread
// of importOriginal), which matters because the axios instance and the
// analytics module are wired through it.
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  DEMO_MODE: true,
}))

vi.mock('@/lib/api/services', () => ({
  backtest: vi.fn(),
  backtestAsync: vi.fn(),
  backtestJobStatus: vi.fn(),
  downloadBlob: vi.fn(),
  exportBacktestCsv: vi.fn(),
}))

// The panes around the results are stubbed. What this suite asserts is WHERE
// the disclaimer sits — inside the result block, beside the KPI grid — and the
// KPI cards and the disclaimer itself stay real. Rendering Recharts,
// framer-motion charts and the resizable split panes adds a lot of work to a
// jsdom worker for no extra signal about placement.
vi.mock('@/components/charts/EquityCurve', () => ({
  EquityCurve: () => <div data-testid="equity-curve" />,
}))
vi.mock('@/components/backtest/SplitPane', () => ({
  SplitPane: ({ first, second }: { first: unknown; second: unknown }) => (
    <div>
      {first as never}
      {second as never}
    </div>
  ),
}))
vi.mock('@/components/backtest/ChatPanel', () => ({ ChatPanel: () => <div /> }))
vi.mock('@/components/backtest/CodePanel', () => ({
  CodePanel: () => <div>generated strategy code</div>,
}))
vi.mock('@/components/backtest/ComparePanel', () => ({ ComparePanel: () => <div /> }))

import * as services from '@/lib/api/services'
import { Backtest } from './Backtest'

// A result with ONE tested symbol, because that is the branch that renders the
// KPI grid — and the disclaimer has to be inside that same block.
const RESULT: BacktestResponse = {
  query: 'undervalued small-cap tech stocks',
  strategy: { buy_rules: ['rsi < 30.0'], sell_rules: ['rsi > 70'], fee_rate: 0.001 },
  results: {
    AAA: {
      symbol: 'AAA',
      initial_capital: 100_000,
      final_value: 143_250,
      total_return_pct: 43.25,
      sharpe_ratio: 1.21,
      max_drawdown_pct: -18.4,
      win_rate_pct: 58.3,
      total_trades: 24,
      trades: [],
      equity_curve: [
        { date: '2016-01-04', value: 100_000 },
        { date: '2021-06-01', value: 121_500 },
        { date: '2026-01-02', value: 143_250 },
      ],
      drawdown_curve: [{ date: '2016-01-04', value: 0 }],
      warnings: [],
      benchmark_total_return_pct: 31.1,
      alpha_pct: 2.4,
      beta: 1.05,
      data_quality: [],
    },
  },
  total_symbols_tested: 1,
}

import userEvent from '@testing-library/user-event'
import { beforeEach } from 'vitest'

const renderWithResult = () =>
  render(
    <TooltipProvider>
      <MemoryRouter initialEntries={['/app/backtest?q=oversold+tech']}>
        <Routes>
          <Route path="/app/backtest" element={<Backtest />} />
        </Routes>
      </MemoryRouter>
    </TooltipProvider>,
  )

beforeEach(() => {
  window.localStorage.clear()
  vi.mocked(services.backtest).mockReset().mockResolvedValue(RESULT)
})

// The page renders a mobile and a desktop copy of the results (one CSS-hidden),
// so every control legitimately appears twice; act on the first.
const first = async (name: RegExp) => (await screen.findAllByRole('button', { name }))[0]

describe('backtest: five layers, simple on the surface', () => {
  it('shows the result with layers 3–5 collapsed', async () => {
    renderWithResult()
    expect(await screen.findAllByText('Total return')).not.toHaveLength(0)
    for (const name of [/Show the rules/, /Adjust & compare/, /Show the code/]) {
      expect(await first(name)).toHaveAttribute('aria-expanded', 'false')
    }
    expect(screen.queryByText('generated strategy code')).toBeNull()
    expect(screen.queryByText(/Tuning is not testing/)).toBeNull()
  })

  it('reaches the generated code from the result in one click — by keyboard too', async () => {
    renderWithResult()
    const code = await first(/Show the code/)
    code.focus()
    await userEvent.keyboard('{Enter}')
    expect(code).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByText('generated strategy code').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText('Raw strategy configuration, JSON')[0]).toHaveTextContent(
      '"buy_rules"',
    )
  })

  it('remembers open layers across a reload', async () => {
    const { unmount } = renderWithResult()
    await userEvent.click(await first(/Show the rules/))
    unmount()
    renderWithResult()
    expect(await first(/Show the rules/)).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByText('RSI(14) below 30').length).toBeGreaterThan(0)
  })

  it('re-runs an edited rule with structured fields and the same query', async () => {
    renderWithResult()
    await userEvent.click(await first(/Show the rules/))
    const buy = screen.getAllByLabelText('Buy when RSI below')[0]
    await userEvent.clear(buy)
    await userEvent.type(buy, '25')
    await userEvent.click(screen.getAllByRole('button', { name: /Re-run with these rules/ })[0])
    const calls = vi.mocked(services.backtest).mock.calls
    const last = calls[calls.length - 1][0]
    expect(last.query).toBe('oversold tech')
    expect(last.rsi_buy_below).toBe(25)
    expect(last.rsi_sell_above).toBe(70)
  })

  it('warns about overfitting once the same strategy has been re-run three times', async () => {
    renderWithResult()
    await userEvent.click(await first(/Adjust & compare/))
    expect(screen.getAllByText('Tuning is not testing.').length).toBeGreaterThan(0)
    for (let i = 0; i < 3; i++) {
      await userEvent.click(screen.getAllByRole('button', { name: /Re-run with parameters/ })[0])
      await screen.findAllByText('Total return')
    }
    expect((await screen.findAllByText(/re-run this strategy 3 times/)).length).toBeGreaterThan(0)
  })
})
