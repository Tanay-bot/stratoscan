import type { BacktestRequest } from './api/types'

/**
 * Serialize a full backtest request into URL search params (share link).
 *
 * Only non-null, non-undefined, non-empty values are written so the URL
 * stays short and defaults are never baked in. parseParams is the inverse.
 */
export function serializeParams(req: BacktestRequest): string {
  const sp = new URLSearchParams()
  const put = (k: string, v: string | number | boolean | null | undefined) => {
    if (v !== null && v !== undefined && v !== '') sp.set(k, String(v))
  }
  put('q', req.query)
  put('top_n', req.top_n)
  put('stop_loss', req.stop_loss_pct)
  put('take_profit', req.take_profit_pct)
  put('allow_short', req.allow_short)
  put('start', req.start_date)
  put('end', req.end_date)
  put('allocation', req.portfolio_allocation)
  put('rebalance', req.rebalance_frequency)
  put('reinvest', req.dividend_reinvest)
  put('borrow', req.borrow_rate_pct)
  put('sector', req.max_sector_exposure)
  return sp.toString()
}

/**
 * Rehydrate tune-drawer state from a shareable URL's search params.
 *
 * Every numeric field falls back to a sensible default when absent or
 * non-numeric, so a bare `?q=AAPL` link works without the other params.
 */
export function parseParams(sp: URLSearchParams) {
  const n = (k: string, d: number) => {
    const v = Number(sp.get(k))
    return Number.isFinite(v) && sp.get(k) !== null ? v : d
  }
  return {
    topN: n('top_n', 5),
    stopLoss: n('stop_loss', 8),
    takeProfit: n('take_profit', 20),
    allowShort: sp.get('allow_short') === 'true',
    startDate: sp.get('start') ?? '',
    endDate: sp.get('end') ?? '',
    allocation: (sp.get('allocation') as 'equal' | 'risk_based') ?? 'equal',
    rebalance: (sp.get('rebalance') as 'none' | 'monthly' | 'quarterly' | 'yearly') ?? 'none',
    reinvest: sp.get('reinvest') === 'true',
    borrow: n('borrow', 0),
    sector: n('sector', 0),
  }
}
