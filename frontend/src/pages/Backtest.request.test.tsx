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
vi.mock('@/components/backtest/CodePanel', () => ({ CodePanel: () => <div /> }))
vi.mock('@/components/backtest/ComparePanel', () => ({ ComparePanel: () => <div /> }))

import * as services from '@/lib/api/services'
import { Backtest } from './Backtest'

// A result with ONE tested symbol, because that is the branch that renders the
// KPI grid — and the disclaimer has to be inside that same block.
const RESULT: BacktestResponse = {
  query: 'undervalued small-cap tech stocks',
  strategy: { entry: 'rsi < 30', exit: 'rsi > 70' },
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

const renderAt = (url: string) =>
  render(
    <TooltipProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/app/backtest" element={<Backtest />} />
        </Routes>
      </MemoryRouter>
    </TooltipProvider>,
  )

// Day 9: what the Tune panel shows must be what the engine runs.
describe('backtest request: only what the user set', () => {
  it('sends the query exactly as typed — no appended RSI/SMA text, no default stop or target', async () => {
    vi.mocked(services.backtest).mockClear().mockResolvedValue(RESULT)
    renderAt('/app/backtest?q=large+cap+stocks+with+bullish+momentum')
    expect(await screen.findAllByText('Total return')).not.toHaveLength(0)
    const req = vi.mocked(services.backtest).mock.calls[0][0]
    expect(req.query).toBe('large cap stocks with bullish momentum')
    expect(req.stop_loss_pct ?? null).toBeNull()
    expect(req.take_profit_pct ?? null).toBeNull()
    expect(req.rsi_buy_below ?? null).toBeNull()
    expect(req.rsi_sell_above ?? null).toBeNull()
  })

  it('carries Tune overrides from a shared link as structured fields', async () => {
    vi.mocked(services.backtest).mockClear().mockResolvedValue(RESULT)
    renderAt('/app/backtest?q=oversold+tech&rsi_buy=35&rsi_sell=65&stop_loss=7')
    expect(await screen.findAllByText('Total return')).not.toHaveLength(0)
    const req = vi.mocked(services.backtest).mock.calls[0][0]
    expect(req.query).toBe('oversold tech')
    expect(req.rsi_buy_below).toBe(35)
    expect(req.rsi_sell_above).toBe(65)
    expect(req.stop_loss_pct).toBe(7)
    expect(req.take_profit_pct ?? null).toBeNull()
  })
})
