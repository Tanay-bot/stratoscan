import { describe, expect, it } from 'vitest'
import { serializeParams, parseParams } from './backtestParams'
import type { BacktestRequest } from './api/types'

describe('serializeParams', () => {
  const minimal: BacktestRequest = { query: 'AAPL', top_n: 5 }

  it('serializes the query and top_n', () => {
    const s = serializeParams(minimal)
    expect(s).toContain('q=AAPL')
    expect(s).toContain('top_n=5')
  })

  it('skips null/undefined values to keep URLs short', () => {
    const s = serializeParams(minimal)
    expect(s).not.toContain('stop_loss')
    expect(s).not.toContain('take_profit')
    expect(s).not.toContain('start')
    expect(s).not.toContain('end')
    expect(s).not.toContain('allow_short')
  })

  it('serializes all non-null fields', () => {
    const full: BacktestRequest = {
      query: 'oversold tech',
      top_n: 10,
      stop_loss_pct: 8,
      take_profit_pct: 20,
      allow_short: true,
      start_date: '2016-01-01',
      end_date: '2026-01-01',
      portfolio_allocation: 'risk_based',
      rebalance_frequency: 'monthly',
      dividend_reinvest: true,
      borrow_rate_pct: 2.5,
      max_sector_exposure: 3,
    }
    const s = serializeParams(full)
    expect(s).toContain('q=oversold+tech')
    expect(s).toContain('top_n=10')
    expect(s).toContain('stop_loss=8')
    expect(s).toContain('take_profit=20')
    expect(s).toContain('allow_short=true')
    expect(s).toContain('start=2016-01-01')
    expect(s).toContain('end=2026-01-01')
    expect(s).toContain('allocation=risk_based')
    expect(s).toContain('rebalance=monthly')
    expect(s).toContain('reinvest=true')
    expect(s).toContain('borrow=2.5')
    expect(s).toContain('sector=3')
  })

  it('serializes false booleans', () => {
    const s = serializeParams({ ...minimal, allow_short: false })
    // false is not null/undefined/empty, so it IS serialized
    expect(s).toContain('allow_short=false')
  })
})

describe('parseParams', () => {
  it('returns defaults for an empty URLSearchParams', () => {
    const p = parseParams(new URLSearchParams())
    expect(p.topN).toBe(5)
    // Day 9: no stop or target unless one was set — an absent override stays
    // absent (it used to default to an 8% stop and 20% target, silently).
    expect(p.stopLoss).toBeNull()
    expect(p.takeProfit).toBeNull()
    expect(p.rsiBuy).toBeNull()
    expect(p.rsiSell).toBeNull()
    expect(p.allowShort).toBe(false)
    expect(p.startDate).toBe('')
    expect(p.endDate).toBe('')
    expect(p.allocation).toBe('equal')
    expect(p.rebalance).toBe('none')
    expect(p.reinvest).toBe(false)
    expect(p.borrow).toBe(0)
    expect(p.sector).toBe(0)
  })

  it('parses all fields from a fully-specified URL', () => {
    const sp = new URLSearchParams(
      'q=oversold+tech&top_n=10&stop_loss=5&take_profit=15&allow_short=true&start=2020-01-01&end=2025-12-31&allocation=risk_based&rebalance=quarterly&reinvest=true&borrow=3&sector=2',
    )
    const p = parseParams(sp)
    expect(p.topN).toBe(10)
    expect(p.stopLoss).toBe(5)
    expect(p.takeProfit).toBe(15)
    expect(p.allowShort).toBe(true)
    expect(p.startDate).toBe('2020-01-01')
    expect(p.endDate).toBe('2025-12-31')
    expect(p.allocation).toBe('risk_based')
    expect(p.rebalance).toBe('quarterly')
    expect(p.reinvest).toBe(true)
    expect(p.borrow).toBe(3)
    expect(p.sector).toBe(2)
  })

  it('falls back to defaults for non-numeric values', () => {
    const sp = new URLSearchParams('top_n=abc&stop_loss=xyz')
    const p = parseParams(sp)
    expect(p.topN).toBe(5) // default
    expect(p.stopLoss).toBeNull() // unset, not a made-up default
  })

  it('treats missing allocation/rebalance as defaults', () => {
    const sp = new URLSearchParams('q=AAPL')
    const p = parseParams(sp)
    expect(p.allocation).toBe('equal')
    expect(p.rebalance).toBe('none')
  })
})

describe('serializeParams ↔ parseParams round-trip', () => {
  it('round-trips a minimal request', () => {
    const req: BacktestRequest = { query: 'AAPL', top_n: 5 }
    const s = serializeParams(req)
    const sp = new URLSearchParams(s)
    const p = parseParams(sp)
    expect(p.topN).toBe(5)
    expect(p.stopLoss).toBeNull()
    expect(p.allowShort).toBe(false) // default
  })

  it('round-trips the Tune RSI overrides', () => {
    const req: BacktestRequest = { query: 'AAPL', rsi_buy_below: 35, rsi_sell_above: 65 }
    const p = parseParams(new URLSearchParams(serializeParams(req)))
    expect(p.rsiBuy).toBe(35)
    expect(p.rsiSell).toBe(65)
  })

  it('round-trips a fully-specified request', () => {
    const req: BacktestRequest = {
      query: 'buy oversold tech with momentum',
      top_n: 15,
      stop_loss_pct: 10,
      take_profit_pct: 25,
      allow_short: true,
      start_date: '2018-06-01',
      end_date: '2026-03-15',
      portfolio_allocation: 'risk_based',
      rebalance_frequency: 'quarterly',
      dividend_reinvest: true,
      borrow_rate_pct: 1.5,
      max_sector_exposure: 4,
    }
    const s = serializeParams(req)
    const sp = new URLSearchParams(s)
    const p = parseParams(sp)
    expect(p.topN).toBe(15)
    expect(p.stopLoss).toBe(10)
    expect(p.takeProfit).toBe(25)
    expect(p.allowShort).toBe(true)
    expect(p.startDate).toBe('2018-06-01')
    expect(p.endDate).toBe('2026-03-15')
    expect(p.allocation).toBe('risk_based')
    expect(p.rebalance).toBe('quarterly')
    expect(p.reinvest).toBe(true)
    expect(p.borrow).toBe(1.5)
    expect(p.sector).toBe(4)
  })

  it('produces a valid URLSearchParams string', () => {
    const req: BacktestRequest = { query: 'test', top_n: 3 }
    const s = serializeParams(req)
    // Must be parseable without throwing
    const sp = new URLSearchParams(s)
    expect(sp.get('q')).toBe('test')
    expect(sp.get('top_n')).toBe('3')
  })
})
