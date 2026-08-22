import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ComparePanel } from './ComparePanel'
import type { BacktestResponse } from '@/lib/api/types'

// Stub recharts so the chart doesn't need a real DOM layout (jsdom has no
// getBoundingClientRect / SVG rendering). The component tests the DATA and
// METRICS, not the chart rendering — the EquityCurve test covers that.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ComposedChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart">{children}</div>
  ),
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  Legend: () => null,
  Line: () => null,
}))

function makeResponse(
  sym: string,
  overrides: Partial<BacktestResponse['results'][string]> = {},
): BacktestResponse {
  const { symbol: _, ...rest } = overrides
  const item = {
    initial_capital: 100000,
    final_value: 120000,
    total_return_pct: 20,
    sharpe_ratio: 1.5,
    max_drawdown_pct: -10,
    win_rate_pct: 55,
    total_trades: 12,
    trades: [],
    equity_curve: [
      { date: '2026-01-01', value: 100000 },
      { date: '2026-06-01', value: 120000 },
    ],
    drawdown_curve: [],
    warnings: [],
    ...rest,
    symbol: sym,
  }
  return {
    query: 'test',
    strategy: {},
    total_symbols_tested: 1,
    results: { [sym]: item },
  }
}

describe('ComparePanel', () => {
  const baseline = makeResponse('AAPL', { total_return_pct: 20, sharpe_ratio: 1.5 })
  const challenger = makeResponse('AAPL', { total_return_pct: 30, sharpe_ratio: 1.2 })

  it('renders metric table with baseline and challenger values', () => {
    render(<ComparePanel baseline={baseline} challenger={challenger} onClear={vi.fn()} />)

    // Same symbol appears in shared-symbols text
    expect(screen.getByText(/AAPL/)).toBeInTheDocument()

    // The metric table should show Total return, Sharpe, Max drawdown, Win rate, Trades
    const rows = screen.getAllByRole('row')
    // header row + 5 metric rows = 6 rows
    expect(rows.length).toBe(6)

    // Values are rendered in the table
    expect(screen.getByText('+20.0%')).toBeInTheDocument() // baseline return
    expect(screen.getByText('+30.0%')).toBeInTheDocument() // challenger return
    expect(screen.getByText('1.50')).toBeInTheDocument() // baseline sharpe
    expect(screen.getByText('1.20')).toBeInTheDocument() // challenger sharpe
  })

  it('shows the winner badge when challenger beats baseline', () => {
    render(<ComparePanel baseline={baseline} challenger={challenger} onClear={vi.fn()} />)
    expect(screen.getByText('Challenger wins')).toBeInTheDocument()
  })

  it('shows baseline wins when baseline is better', () => {
    const betterBase = makeResponse('AAPL', {
      total_return_pct: 50,
      sharpe_ratio: 2.0,
    })
    const worseChal = makeResponse('AAPL', {
      total_return_pct: 10,
      sharpe_ratio: 0.8,
    })
    render(<ComparePanel baseline={betterBase} challenger={worseChal} onClear={vi.fn()} />)
    expect(screen.getByText('Baseline wins')).toBeInTheDocument()
  })

  it('shows no winner badge when returns are equal', () => {
    const same1 = makeResponse('AAPL', { total_return_pct: 20 })
    const same2 = makeResponse('AAPL', { total_return_pct: 20 })
    render(<ComparePanel baseline={same1} challenger={same2} onClear={vi.fn()} />)
    expect(screen.queryByText('Baseline wins')).toBeNull()
    expect(screen.queryByText('Challenger wins')).toBeNull()
  })

  it('calls onClear when the X button is clicked', async () => {
    const onClear = vi.fn()
    render(<ComparePanel baseline={baseline} challenger={challenger} onClear={onClear} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Clear comparison' }))
    expect(onClear).toHaveBeenCalledOnce()
  })

  it('handles portfolio-level metrics when portfolio is present', () => {
    const withPortfolio: BacktestResponse = {
      ...baseline,
      portfolio: {
        initial_capital: 100000,
        total_return_pct: 35,
        sharpe_ratio: 1.8,
        max_drawdown_pct: -8,
        final_value: 135000,
        equity_curve: [
          { date: '2026-01-01', value: 100000 },
          { date: '2026-06-01', value: 135000 },
        ],
        drawdown_curve: [],
        weights: { AAPL: 0.5, MSFT: 0.5 },
        symbols: ['AAPL', 'MSFT'],
        allocation: 'equal',
        rebalance: 'none',
      },
    }
    render(<ComparePanel baseline={withPortfolio} challenger={challenger} onClear={vi.fn()} />)
    // Should show the combined portfolio return, not individual symbol returns
    expect(screen.getByText('+35.0%')).toBeInTheDocument()
  })
})
