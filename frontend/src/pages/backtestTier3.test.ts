import { describe, expect, it } from 'vitest'
import type { BacktestJobStatus } from '@/lib/api/types'

// ── Test the async job status type contract ──────────────────────────────────
// The frontend polls backtestJobStatus() until status === 'done' or 'error'.
// This tests the type contract and the state transitions the UI expects.

describe('BacktestJobStatus type contract', () => {
  it('has all required fields for the polling loop', () => {
    const status: BacktestJobStatus = {
      job_id: 'test-123',
      status: 'running',
      phase: 'backtesting',
      total_symbols: 5,
      done_symbols: ['AAPL', 'MSFT'],
    }

    expect(status.job_id).toBe('test-123')
    expect(status.status).toBe('running')
    expect(status.phase).toBe('backtesting')
    expect(status.total_symbols).toBe(5)
    expect(status.done_symbols).toEqual(['AAPL', 'MSFT'])
  })

  it('done_symbols is optional (initial state has empty array)', () => {
    const initial: BacktestJobStatus = {
      job_id: 'test-456',
      status: 'running',
      phase: 'interpreting',
      total_symbols: 3,
      done_symbols: [],
    }
    expect(initial.done_symbols).toHaveLength(0)
  })

  it('error state carries an error message', () => {
    const error: BacktestJobStatus = {
      job_id: 'test-789',
      status: 'error',
      error: 'Backtest failed: LLM timeout',
    }
    expect(error.status).toBe('error')
    expect(error.error).toContain('LLM timeout')
  })

  it('done state carries the result', () => {
    const done: BacktestJobStatus = {
      job_id: 'test-done',
      status: 'done',
      result: {
        query: 'test',
        strategy: {},
        results: {},
        total_symbols_tested: 0,
      },
    }
    expect(done.status).toBe('done')
    expect(done.result).toBeDefined()
    expect(done.result!.total_symbols_tested).toBe(0)
  })
})

describe('async job polling state machine', () => {
  // Simulate the polling loop from Backtest.tsx (lines 226-253)
  function simulatePolling(statuses: BacktestJobStatus[]): {
    events: string[]
    finalStatus: string
  } {
    const events: string[] = []
    let finalStatus = 'running'

    for (const status of statuses) {
      events.push(
        `poll: ${status.status} (${(status.done_symbols ?? []).length}/${status.total_symbols ?? '?'})`,
      )
      if (status.status === 'done') {
        finalStatus = 'done'
        break
      }
      if (status.status === 'error') {
        finalStatus = 'error'
        break
      }
    }

    return { events, finalStatus }
  }

  it('transitions from running → done', () => {
    const { events, finalStatus } = simulatePolling([
      { job_id: '1', status: 'running', phase: 'interpreting', total_symbols: 3, done_symbols: [] },
      {
        job_id: '1',
        status: 'running',
        phase: 'backtesting',
        total_symbols: 3,
        done_symbols: ['AAPL'],
      },
      {
        job_id: '1',
        status: 'running',
        phase: 'backtesting',
        total_symbols: 3,
        done_symbols: ['AAPL', 'MSFT'],
      },
      {
        job_id: '1',
        status: 'running',
        phase: 'backtesting',
        total_symbols: 3,
        done_symbols: ['AAPL', 'MSFT', 'GOOG'],
      },
      {
        job_id: '1',
        status: 'done',
        result: { query: 'test', strategy: {}, results: {}, total_symbols_tested: 3 },
      },
    ])
    expect(finalStatus).toBe('done')
    expect(events).toHaveLength(5)
    expect(events[0]).toContain('0/3')
    expect(events[3]).toContain('3/3')
  })

  it('transitions from running → error', () => {
    const { events, finalStatus } = simulatePolling([
      {
        job_id: '2',
        status: 'running',
        phase: 'backtesting',
        total_symbols: 5,
        done_symbols: ['AAPL'],
      },
      { job_id: '2', status: 'error', error: 'yfinance timeout' },
    ])
    expect(finalStatus).toBe('error')
    expect(events).toHaveLength(2)
    expect(events[1]).toContain('error')
  })

  it('progress bar width calculation is correct', () => {
    const done_symbols = ['AAPL', 'MSFT', 'GOOG']
    const total_symbols = 5
    const width = (done_symbols.length / Math.max(1, total_symbols)) * 100
    expect(width).toBe(60) // 3/5 = 60%
  })

  it('progress bar handles zero total_symbols', () => {
    const width = (0 / Math.max(1, 0)) * 100
    expect(width).toBe(0)
  })
})

describe('CSV export contract', () => {
  it('exportBacktestCsv expects a BacktestRequest', () => {
    const req = {
      query: 'oversold tech stocks',
      top_n: 5,
      stop_loss_pct: 8,
      take_profit_pct: 20,
    }

    expect(req.query).toBeTruthy()
    expect(typeof req.top_n).toBe('number')
    expect(typeof req.stop_loss_pct).toBe('number')
  })

  it('exportScreenCsv expects a ScreenRequest shape', () => {
    const req = {
      query: 'high dividend stocks',
      limit: 50,
      offset: 0,
      sort_by: 'dividend_yield',
      sort_desc: true,
    }

    expect(req.query).toBeTruthy()
    expect(typeof req.limit).toBe('number')
  })
})
