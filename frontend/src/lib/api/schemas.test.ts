import { describe, expect, it } from 'vitest'
import {
  authResponseSchema,
  backtestResponseSchema,
  newsFeedResponseSchema,
  screenResponseSchema,
  stocksResponseSchema,
} from './schemas'

/**
 * Behavioral tests for the zod API boundary. These assert the contract the
 * way the backend (src/api.py) actually serializes it: a payload shaped like
 * the real /screen response must parse; a payload where the backend renamed
 * a required field must FAIL (that's the drift detector working).
 */
describe('zod API boundary — behavioral', () => {
  it('accepts a real-shaped /screen payload', () => {
    const payload = {
      query: 'momentum breakout stocks',
      interpreted_filters: { rsi_14: ['gte', 55], sort_by: 'score' },
      total_matches: 3,
      results: [
        {
          symbol: 'NVDA',
          company_name: 'NVIDIA Corporation',
          sector: 'Technology',
          industry: 'Semiconductors',
          current_price: 142.5,
          price_change_pct: 2.4,
          market_cap: 3500000000000,
          pe_ratio: 55.2,
          rsi_14: 61.0,
          volume: 34000000,
          dividend_yield: 0.03,
          beta: 1.7,
          sentiment: { bullish: 0.6 },
          score: 82.5,
          score_components: { value: 71, momentum: 88 },
          why_matched: ['above SMA-50'],
          is_stale: false,
          data_age_minutes: 4,
        },
      ],
      limit: 10,
      offset: 0,
      has_more: false,
      next_offset: 10,
      sort_by: 'score',
      sort_desc: true,
    }
    const parsed = screenResponseSchema.safeParse(payload)
    expect(parsed.success).toBe(true)
    if (parsed.success) {
      expect(parsed.data.results[0].symbol).toBe('NVDA')
      expect(parsed.data.total_matches).toBe(3)
    }
  })

  it('REJECTS a payload where the backend renamed a required field (symbol → ticker)', () => {
    const payload = {
      query: 'x',
      interpreted_filters: {},
      total_matches: 0,
      results: [{ ticker: 'NVDA', company_name: 'NVIDIA' }], // renamed — must fail
      limit: 10,
      offset: 0,
      has_more: false,
    }
    const parsed = screenResponseSchema.safeParse(payload)
    expect(parsed.success).toBe(false)
    // The error should name the missing/offending field.
    const msg = JSON.stringify(parsed.error?.issues ?? [])
    expect(msg).toContain('symbol')
  })

  it('is forward-compatible: backend ADDING fields still parses (passthrough)', () => {
    const payload = {
      query: 'x',
      interpreted_filters: {},
      total_matches: 1,
      results: [{ symbol: 'NVDA', company_name: 'NVIDIA', brand_new_field: 1 }],
      limit: 10,
      offset: 0,
      has_more: false,
    }
    expect(screenResponseSchema.safeParse(payload).success).toBe(true)
  })

  it('accepts a real-shaped /backtest payload with trades + equity curve', () => {
    const payload = {
      query: 'rsi oversold bounce',
      strategy: { rsi_14: ['lt', 30] },
      total_symbols_tested: 2,
      results: {
        NVDA: {
          symbol: 'NVDA',
          initial_capital: 10000,
          final_value: 12340.5,
          total_return_pct: 23.4,
          sharpe_ratio: 1.2,
          max_drawdown_pct: -8.1,
          win_rate_pct: 62.5,
          total_trades: 8,
          trades: [
            {
              symbol: 'NVDA',
              buy_date: '2026-01-05',
              buy_price: 100,
              sell_date: '2026-02-01',
              sell_price: 110,
              shares: 100,
              pnl: 1000,
              pnl_pct: 10,
              side: 'long',
            },
          ],
          equity_curve: [{ date: '2026-01-05', value: 10000 }],
          benchmark_total_return_pct: 5.2,
          alpha_pct: 18.2,
        },
      },
    }
    expect(backtestResponseSchema.safeParse(payload).success).toBe(true)
  })

  it('accepts a real-shaped /news payload', () => {
    const payload = {
      total_articles: 2,
      returned: 2,
      articles: [
        {
          symbol: 'NVDA',
          headline: 'NVIDIA beats estimates',
          source: 'Reuters',
          published_at: '2026-08-03T00:00:00Z',
          sentiment: 'bullish',
          sentiment_score: 0.8,
        },
      ],
    }
    expect(newsFeedResponseSchema.safeParse(payload).success).toBe(true)
  })

  it('accepts a real-shaped /stocks list payload', () => {
    const payload = {
      total_in_database: 5000,
      returned: 2,
      limit: 10,
      offset: 0,
      results: [
        { symbol: 'NVDA', company_name: 'NVIDIA Corporation', current_price: 142.5 },
        { symbol: 'AAPL', company_name: 'Apple Inc.', current_price: 212.4 },
      ],
    }
    expect(stocksResponseSchema.safeParse(payload).success).toBe(true)
  })

  it('accepts a real-shaped /auth/login payload', () => {
    const payload = {
      access_token: 'jwt.abc.def',
      token_type: 'bearer',
      expires_in: 604800,
      email_sent: true,
      user: {
        id: 1,
        email: 'a@b.co',
        plan: 'free',
        email_verified: true,
        is_admin: false,
        created_at: '2026-01-01T00:00:00Z',
      },
    }
    expect(authResponseSchema.safeParse(payload).success).toBe(true)
  })
})
