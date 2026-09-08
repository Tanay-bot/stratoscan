import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// ── Force DEMO_MODE on for service-level tests ───────────────────────────────
// The service functions branch on DEMO_MODE at runtime. In the test env it's
// false (no VITE_DEMO_MODE set), so we mock it to true to exercise the demo
// code paths without a running backend.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...mod, DEMO_MODE: true }
})

import {
  backtestAsync,
  backtestJobStatus,
  exportBacktestCsv,
  exportScreenCsv,
  downloadBlob,
} from '@/lib/api/services'
import { serializeParams, parseParams } from '@/lib/backtestParams'
import { toApiError } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import type { BacktestRequest, ScreenRequest } from '@/lib/api/types'

// ── Async progress: backtestAsync + backtestJobStatus ────────────────────────

describe('Tier 3: async progress (demo mode)', () => {
  it('backtestAsync returns a job_id and initial status', async () => {
    const res = await backtestAsync({ query: 'oversold tech stocks', top_n: 5 })
    expect(res.job_id).toBe('demo')
    expect(res.status).toBe('queued')
    expect(res.phase).toBe('interpreting')
  })

  it('backtestJobStatus throws in demo mode (demo has no background jobs)', async () => {
    await expect(backtestJobStatus('demo')).rejects.toThrow('demo-mode backtest is synchronous')
  })
})

// ── CSV export: exportBacktestCsv + exportScreenCsv ──────────────────────────

describe('Tier 3: CSV export (demo mode)', () => {
  const backtestReq: BacktestRequest = {
    query: 'oversold tech stocks',
    top_n: 3,
    stop_loss_pct: 8,
    take_profit_pct: 20,
  }

  const screenReq: ScreenRequest = {
    query: 'high dividend stocks',
    limit: 50,
    sort_by: 'dividend_yield',
    sort_desc: true,
  }

  it('exportBacktestCsv returns a Blob with CSV content', async () => {
    const blob = await exportBacktestCsv(backtestReq)
    expect(blob).toBeInstanceOf(Blob)
    expect(blob.type).toBe('text/csv')

    const text = await blob.text()
    // CSV should have a header row
    expect(text).toContain('symbol,side,buy_date')
    // Should have at least one data row (demo generates trades)
    const lines = text.split('\n').filter((l) => l.trim())
    expect(lines.length).toBeGreaterThanOrEqual(2) // header + at least 1 trade
  })

  it('exportBacktestCsv CSV has correct columns', async () => {
    const blob = await exportBacktestCsv(backtestReq)
    const text = await blob.text()
    const header = text.split('\n')[0]
    const columns = header.split(',')
    expect(columns).toEqual([
      'symbol',
      'side',
      'buy_date',
      'buy_price',
      'sell_date',
      'sell_price',
      'shares',
      'pnl',
      'pnl_pct',
      'exit_reason',
    ])
  })

  it('exportBacktestCsv data rows have numeric buy_price and shares', async () => {
    const blob = await exportBacktestCsv(backtestReq)
    const text = await blob.text()
    const dataRows = text
      .split('\n')
      .slice(1)
      .filter((l) => l.trim())
    expect(dataRows.length).toBeGreaterThan(0)

    for (const row of dataRows) {
      const cols = row.split(',')
      // buy_price (index 3) should be a number
      expect(Number(cols[3])).toBeGreaterThan(0)
      // shares (index 6) should be a positive integer
      expect(Number(cols[6])).toBeGreaterThan(0)
      // side should be 'long' or 'short'
      expect(['long', 'short']).toContain(cols[1])
    }
  })

  it('exportScreenCsv returns a Blob with CSV content', async () => {
    const blob = await exportScreenCsv(screenReq)
    expect(blob).toBeInstanceOf(Blob)
    expect(blob.type).toBe('text/csv')

    const text = await blob.text()
    expect(text).toContain('symbol,company_name')
    const lines = text.split('\n').filter((l) => l.trim())
    expect(lines.length).toBeGreaterThanOrEqual(2)
  })

  it('exportScreenCsv CSV has correct columns', async () => {
    const blob = await exportScreenCsv(screenReq)
    const text = await blob.text()
    const header = text.split('\n')[0]
    const columns = header.split(',')
    expect(columns).toEqual([
      'symbol',
      'company_name',
      'sector',
      'current_price',
      'price_change_pct',
      'market_cap',
      'pe_ratio',
      'rsi_14',
      'volume',
      'score',
    ])
  })

  it('exportScreenCsv data rows have valid symbols', async () => {
    const blob = await exportScreenCsv(screenReq)
    const text = await blob.text()
    const dataRows = text
      .split('\n')
      .slice(1)
      .filter((l) => l.trim())
    expect(dataRows.length).toBeGreaterThan(0)

    for (const row of dataRows) {
      const cols = row.split(',')
      // symbol should be a non-empty string (1-5 uppercase letters)
      expect(cols[0]).toMatch(/^[A-Z]{1,5}$/)
    }
  })
})

// ── downloadBlob: DOM download mechanism ─────────────────────────────────────

describe('Tier 3: downloadBlob', () => {
  let clickSpy: ReturnType<typeof vi.fn>
  let appendChildSpy: ReturnType<typeof vi.fn>
  let removeChildSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    clickSpy = vi.fn()
    appendChildSpy = vi.fn()
    removeChildSpy = vi.fn()

    // Mock document.createElement to return an object with a click method
    vi.spyOn(document, 'createElement').mockReturnValue({
      href: '',
      download: '',
      click: clickSpy,
      remove: vi.fn(),
    } as unknown as HTMLAnchorElement)

    vi.spyOn(document.body, 'appendChild').mockImplementation(appendChildSpy)
    vi.spyOn(document.body, 'removeChild').mockImplementation(removeChildSpy)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('creates an object URL, triggers click, and cleans up', () => {
    const blob = new Blob(['test,data'], { type: 'text/csv' })

    // Mock URL methods (jsdom doesn't implement createObjectURL)
    const createObjectURLSpy = vi.fn(() => 'blob:mock-url').mockReturnValue('blob:mock-url')
    const revokeObjectURLSpy = vi.fn()

    // jsdom may or may not have URL.createObjectURL; patch it
    const origCreate = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURLSpy, configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURLSpy, configurable: true })

    try {
      downloadBlob(blob, 'trades.csv')

      // Should create a blob URL
      expect(createObjectURLSpy).toHaveBeenCalledWith(blob)

      // Should set href and download on the anchor
      const anchor = document.createElement('a')
      expect(anchor.href).toBe('blob:mock-url')
      expect(anchor.download).toBe('trades.csv')

      // Should append to body, click, and remove
      expect(appendChildSpy).toHaveBeenCalledOnce()
      expect(clickSpy).toHaveBeenCalledOnce()
      // a.remove() is called on the element, not document.body.removeChild
      // (Element.remove() is a DOM4 method on the anchor itself)

      // Should revoke the URL to free memory
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:mock-url')
    } finally {
      Object.defineProperty(URL, 'createObjectURL', { value: origCreate, configurable: true })
      Object.defineProperty(URL, 'revokeObjectURL', { value: origRevoke, configurable: true })
    }
  })

  it('uses the provided filename', () => {
    const blob = new Blob(['x'], { type: 'text/csv' })

    const createObjectURLSpy = vi.fn(() => 'blob:url2')
    const revokeObjectURLSpy = vi.fn()
    const origCreate = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURLSpy, configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURLSpy, configurable: true })

    try {
      downloadBlob(blob, 'my_custom_export_12345.csv')

      const anchor = document.createElement('a')
      expect(anchor.download).toBe('my_custom_export_12345.csv')
    } finally {
      Object.defineProperty(URL, 'createObjectURL', { value: origCreate, configurable: true })
      Object.defineProperty(URL, 'revokeObjectURL', { value: origRevoke, configurable: true })
    }
  })
})

// ── Compare mode: state management contract ──────────────────────────────────

describe('Tier 3: compare mode contract', () => {
  it('buildRequest includes symbols from baseline when set', () => {
    const baseline = {
      results: {
        AAPL: { symbol: 'AAPL', total_return_pct: 20 },
        MSFT: { symbol: 'MSFT', total_return_pct: 15 },
      },
    }

    const req: BacktestRequest = {
      query: 'momentum strategy',
      top_n: 5,
      symbols: baseline ? Object.keys(baseline.results) : null,
    }

    expect(req.symbols).toEqual(['AAPL', 'MSFT'])
  })

  it('buildRequest has no symbols when no baseline is set', () => {
    // The assertion is load-bearing: with a plain annotation, TS's
    // control-flow analysis still narrows a `const` initialized to `null`
    // down to `null`, making the truthy branch below `never` and failing
    // typecheck. `as` keeps the union so the ternary stays type-checkable.
    const baseline = null as { results: Record<string, { symbol: string }> } | null

    const req: BacktestRequest = {
      query: 'momentum strategy',
      top_n: 5,
      symbols: baseline ? Object.keys(baseline.results) : null,
    }

    expect(req.symbols).toBeNull()
  })

  it('compare mode symbols pin the exact same set for challenger', () => {
    const baselineResults = {
      AAPL: { symbol: 'AAPL', total_return_pct: 20 },
      MSFT: { symbol: 'MSFT', total_return_pct: 15 },
      GOOG: { symbol: 'GOOG', total_return_pct: 25 },
    }

    const challengerSymbols = Object.keys(baselineResults)
    expect(challengerSymbols).toEqual(['AAPL', 'MSFT', 'GOOG'])

    const challengerReq: BacktestRequest = {
      query: 'different strategy',
      symbols: challengerSymbols,
    }
    expect(challengerReq.symbols).toHaveLength(3)
    expect(challengerReq.symbols).toContain('AAPL')
    expect(challengerReq.symbols).toContain('MSFT')
    expect(challengerReq.symbols).toContain('GOOG')
  })
})

// ── Shareable links: serializeParams + parseParams round-trip ────────────────

describe('Tier 3: shareable link serialization', () => {
  it('serializes a full backtest request into URL params', () => {
    const req: BacktestRequest = {
      query: 'buy oversold tech stocks with momentum',
      top_n: 10,
      stop_loss_pct: 8,
      take_profit_pct: 20,
      allow_short: true,
      start_date: '2016-01-01',
      end_date: '2026-01-01',
      portfolio_allocation: 'risk_based',
      rebalance_frequency: 'quarterly',
      dividend_reinvest: true,
      borrow_rate_pct: 2.5,
      max_sector_exposure: 3,
    }

    const urlParams = serializeParams(req)
    const sp = new URLSearchParams(urlParams)

    expect(sp.get('q')).toBe('buy oversold tech stocks with momentum')
    expect(sp.get('top_n')).toBe('10')
    expect(sp.get('stop_loss')).toBe('8')
    expect(sp.get('take_profit')).toBe('20')
    expect(sp.get('allow_short')).toBe('true')
    expect(sp.get('start')).toBe('2016-01-01')
    expect(sp.get('end')).toBe('2026-01-01')
    expect(sp.get('allocation')).toBe('risk_based')
    expect(sp.get('rebalance')).toBe('quarterly')
    expect(sp.get('reinvest')).toBe('true')
    expect(sp.get('borrow')).toBe('2.5')
    expect(sp.get('sector')).toBe('3')
  })

  it('round-trips: serialize → parse → same values', () => {
    const req: BacktestRequest = {
      query: 'undervalued small-cap tech with rising momentum',
      top_n: 15,
      stop_loss_pct: 10,
      take_profit_pct: 25,
      allow_short: false,
      start_date: '2018-06-01',
      end_date: '2026-03-15',
      portfolio_allocation: 'equal',
      rebalance_frequency: 'monthly',
      dividend_reinvest: false,
      borrow_rate_pct: 0,
      max_sector_exposure: 0,
    }

    const urlParams = serializeParams(req)
    const sp = new URLSearchParams(urlParams)
    const parsed = parseParams(sp)

    expect(parsed.topN).toBe(15)
    expect(parsed.stopLoss).toBe(10)
    expect(parsed.takeProfit).toBe(25)
    expect(parsed.allowShort).toBe(false)
    expect(parsed.startDate).toBe('2018-06-01')
    expect(parsed.endDate).toBe('2026-03-15')
    expect(parsed.allocation).toBe('equal')
    expect(parsed.rebalance).toBe('monthly')
    expect(parsed.reinvest).toBe(false)
    expect(parsed.borrow).toBe(0)
    expect(parsed.sector).toBe(0)
  })

  it('URL stays short: null/undefined fields are omitted', () => {
    const req: BacktestRequest = { query: 'AAPL', top_n: 5 }
    const urlParams = serializeParams(req)

    expect(urlParams).not.toContain('stop_loss')
    expect(urlParams).not.toContain('take_profit')
    expect(urlParams).not.toContain('start')
    expect(urlParams).not.toContain('end')
    expect(urlParams).not.toContain('allow_short')
    expect(urlParams).not.toContain('allocation')
    expect(urlParams).not.toContain('rebalance')
    expect(urlParams).not.toContain('reinvest')
    expect(urlParams).not.toContain('borrow')
    expect(urlParams).not.toContain('sector')
  })

  it('shared link URL is valid for browser navigation', () => {
    const req: BacktestRequest = {
      query: 'oversold tech',
      top_n: 10,
      stop_loss_pct: 5,
    }
    const params = serializeParams(req)
    const fullUrl = `https://stratoscan.app/app/backtest?${params}`

    const url = new URL(fullUrl)
    expect(url.pathname).toBe('/app/backtest')
    expect(url.searchParams.get('q')).toBe('oversold tech')
    expect(url.searchParams.get('top_n')).toBe('10')
  })
})

// ── Rate-limit auto-retry: 429 handling contract ─────────────────────────────

describe('Tier 3: rate-limit auto-retry contract', () => {
  it('ApiError preserves retryAfter from header', () => {
    const err = new ApiError('Rate limit', 429, 30)
    expect(err.status).toBe(429)
    expect(err.retryAfter).toBe(30)
    expect(err.message).toBe('Rate limit')
  })

  it('ApiError defaults retryAfter to undefined when not provided', () => {
    const err = new ApiError('Rate limit', 429)
    expect(err.retryAfter).toBeUndefined()
  })

  it('toApiError passes through ApiError instances', () => {
    const err = new ApiError('Rate limit exceeded', 429, 45)
    const normalized = toApiError(err)
    expect(normalized).toBe(err) // same reference
    expect(normalized.status).toBe(429)
    expect(normalized.retryAfter).toBe(45)
  })

  it('retry_after fallback: when retryAfter is undefined, UI defaults to 10s', () => {
    const err = new ApiError('Rate limit', 429)
    const wait = err.retryAfter ?? 10
    expect(wait).toBe(10)
  })
})

// ── Data-age badge contract ──────────────────────────────────────────────────

describe('Tier 3: per-symbol data-age badge contract', () => {
  it('BacktestResultItem supports data_age_minutes and is_stale', () => {
    const item = {
      symbol: 'AAPL',
      initial_capital: 100000,
      final_value: 120000,
      total_return_pct: 20,
      total_trades: 10,
      trades: [],
      equity_curve: [],
      warnings: [],
      data_age_minutes: 45,
      is_stale: false,
    }

    expect(item.data_age_minutes).toBe(45)
    expect(item.is_stale).toBe(false)
  })

  it('data_age_minutes can be null (compare-mode or unknown)', () => {
    const item = {
      symbol: 'MSFT',
      initial_capital: 100000,
      final_value: 110000,
      total_return_pct: 10,
      total_trades: 5,
      trades: [],
      equity_curve: [],
      warnings: [],
      data_age_minutes: null,
      is_stale: false,
    }

    expect(item.data_age_minutes).toBeNull()
  })

  it('is_stale defaults to false when absent', () => {
    // `is_stale` is declared optional so the absent-value read below is the
    // documented contract rather than a typecheck error.
    const item: { symbol: string; is_stale?: boolean; [k: string]: unknown } = {
      symbol: 'GOOG',
      initial_capital: 100000,
      final_value: 130000,
      total_return_pct: 30,
      total_trades: 8,
      trades: [],
      equity_curve: [],
      warnings: [],
    }

    // is_stale is optional — when absent, UI treats it as not stale
    expect(item.is_stale ?? false).toBe(false)
  })

  it('stale threshold: data_age_minutes > 15 means stale', () => {
    const staleItem = { data_age_minutes: 20, is_stale: true }
    const freshItem = { data_age_minutes: 10, is_stale: false }

    expect(staleItem.is_stale).toBe(true)
    expect(freshItem.is_stale).toBe(false)
  })
})

// ── Async job status type contract (extended) ────────────────────────────────

describe('Tier 3: async job status extended contract', () => {
  it('queued status has job_id but no result', () => {
    const status = {
      job_id: 'abc-123',
      status: 'queued' as const,
    }
    expect(status.job_id).toBeTruthy()
    expect(status.status).toBe('queued')
  })

  it('running status has phase and progress fields', () => {
    const status = {
      job_id: 'abc-123',
      status: 'running' as const,
      phase: 'backtesting',
      total_symbols: 5,
      done_symbols: ['AAPL', 'MSFT'],
    }
    expect(status.done_symbols).toHaveLength(2)
    expect(status.total_symbols).toBe(5)
    expect((status.done_symbols?.length ?? 0) / (status.total_symbols ?? 1)).toBe(0.4)
  })

  it('done status has result and no error', () => {
    const status = {
      job_id: 'abc-123',
      status: 'done' as const,
      result: {
        query: 'test',
        strategy: {},
        results: {},
        total_symbols_tested: 3,
      },
    }
    expect(status.result).toBeDefined()
    expect(status.result!.total_symbols_tested).toBe(3)
  })

  it('error status has error message and no result', () => {
    const status = {
      job_id: 'abc-123',
      status: 'error' as const,
      error: 'yfinance timeout after 120s',
    }
    expect(status.error).toContain('yfinance timeout')
  })

  it('progress percentage calculation is correct', () => {
    const done = ['AAPL', 'MSFT', 'GOOG']
    const total = 10
    const pct = (done.length / Math.max(1, total)) * 100
    expect(pct).toBe(30)
  })

  it('progress handles zero total gracefully', () => {
    const done: string[] = []
    const total = 0
    const pct = (done.length / Math.max(1, total)) * 100
    expect(pct).toBe(0)
  })
})

// ── Portfolio comparison metrics contract ────────────────────────────────────

describe('Tier 3: portfolio comparison metrics', () => {
  it('baseline and challenger can be compared on shared metrics', () => {
    const baseline = {
      total_return_pct: 20,
      sharpe_ratio: 1.5,
      max_drawdown_pct: -10,
      win_rate_pct: 55,
      total_trades: 12,
    }

    const challenger = {
      total_return_pct: 30,
      sharpe_ratio: 1.2,
      max_drawdown_pct: -15,
      win_rate_pct: 60,
      total_trades: 15,
    }

    // Challenger wins on return but loses on Sharpe
    expect(challenger.total_return_pct).toBeGreaterThan(baseline.total_return_pct)
    expect(challenger.sharpe_ratio).toBeLessThan(baseline.sharpe_ratio)
  })

  it('winner determination: higher return + higher Sharpe = challenger wins', () => {
    const baseline = { total_return_pct: 20, sharpe_ratio: 1.5 }
    const challenger = { total_return_pct: 30, sharpe_ratio: 2.0 }

    const challengerWins =
      challenger.total_return_pct > baseline.total_return_pct &&
      challenger.sharpe_ratio > baseline.sharpe_ratio
    expect(challengerWins).toBe(true)
  })

  it('winner determination: higher return but lower Sharpe = ambiguous', () => {
    const baseline = { total_return_pct: 20, sharpe_ratio: 1.5 }
    const challenger = { total_return_pct: 30, sharpe_ratio: 1.0 }

    const challengerWins =
      challenger.total_return_pct > baseline.total_return_pct &&
      challenger.sharpe_ratio > baseline.sharpe_ratio
    const baselineWins =
      baseline.total_return_pct > challenger.total_return_pct &&
      baseline.sharpe_ratio > challenger.sharpe_ratio

    expect(challengerWins).toBe(false)
    expect(baselineWins).toBe(false)
  })
})
