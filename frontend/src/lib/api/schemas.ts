/**
 * Runtime validation of the Stratoscan API contract (src/api.py).
 *
 * TypeScript types are compile-time trust: if the backend changes a field
 * name or shape, the types lie and the UI silently renders undefined. These
 * zod schemas validate the ACTUAL payloads at the API boundary, so contract
 * drift surfaces as a clear error instead of a broken screen.
 *
 * Deliberate design choice: schemas use `.passthrough()` so a backend that
 * ADDS fields never breaks existing clients (forward compatible), while a
 * backend that REMOVES or renames a required field is caught immediately.
 */

import { z } from 'zod'

// ── Core scalars ──────────────────────────────────────────────────────────

const nullableNumber = z.number().nullish()
const optionalString = z.string().nullish()

// ── Screener ──────────────────────────────────────────────────────────────

export const stockResultSchema = z
  .object({
    symbol: z.string(),
    company_name: z.string(),
    sector: optionalString,
    industry: optionalString,
    current_price: nullableNumber,
    price_change_pct: nullableNumber,
    market_cap: nullableNumber,
    pe_ratio: nullableNumber,
    rsi_14: nullableNumber,
    volume: nullableNumber,
    dividend_yield: nullableNumber,
    beta: nullableNumber,
    sentiment: z.record(z.unknown()).nullish(),
    score: nullableNumber,
    score_components: z.record(z.number()).optional(),
    why_matched: z.array(z.string()).optional(),
    is_stale: z.boolean().optional(),
    data_age_minutes: nullableNumber,
  })
  .passthrough()

export const screenResponseSchema = z
  .object({
    query: z.string(),
    interpreted_filters: z.record(z.unknown()),
    total_matches: z.number(),
    results: z.array(stockResultSchema),
    limit: z.number(),
    offset: z.number(),
    has_more: z.boolean(),
    next_offset: z.number().nullish(),
    sort_by: z.string().optional(),
    sort_desc: z.boolean().optional(),
    applied_filters: z.array(z.string()).optional(),
    data_quality: z.record(z.unknown()).optional(),
  })
  .passthrough()

// ── Backtest ──────────────────────────────────────────────────────────────

export const tradeInfoSchema = z
  .object({
    symbol: z.string(),
    buy_date: z.string(),
    buy_price: z.number(),
    sell_date: z.string().nullish(),
    sell_price: z.number().nullish(),
    shares: z.number(),
    pnl: z.number().nullish(),
    pnl_pct: z.number().nullish(),
    side: z.enum(['long', 'short']).optional(),
    exit_reason: z.string().optional(),
  })
  .passthrough()

export const backtestResultItemSchema = z
  .object({
    symbol: z.string(),
    initial_capital: z.number(),
    final_value: z.number(),
    total_return_pct: z.number(),
    sharpe_ratio: nullableNumber,
    max_drawdown_pct: nullableNumber,
    win_rate_pct: nullableNumber,
    total_trades: z.number(),
    trades: z.array(tradeInfoSchema).optional(),
    equity_curve: z
      .array(z.object({ date: z.string(), value: z.number() }).passthrough())
      .optional(),
    drawdown_curve: z
      .array(z.object({ date: z.string(), value: z.number() }).passthrough())
      .optional(),
    warnings: z.array(z.string()).optional(),
    benchmark_total_return_pct: nullableNumber,
    alpha_pct: nullableNumber,
    beta: nullableNumber,
    excess_return_pct: nullableNumber,
  })
  .passthrough()

export const backtestResponseSchema = z
  .object({
    query: z.string(),
    strategy: z.record(z.unknown()),
    results: z.record(backtestResultItemSchema),
    total_symbols_tested: z.number(),
    message: z.string().nullable().optional(),
  })
  .passthrough()

// ── Auth ──────────────────────────────────────────────────────────────────

export const userOutSchema = z
  .object({
    id: z.number(),
    email: z.string(),
    plan: z.string(),
    email_verified: z.boolean(),
    is_admin: z.boolean().optional(),
    created_at: z.string().nullish(),
  })
  .passthrough()

export const authResponseSchema = z
  .object({
    access_token: z.string(),
    token_type: z.string(),
    expires_in: z.number(),
    email_sent: z.boolean().optional(),
    user: userOutSchema,
  })
  .passthrough()

// ── News ──────────────────────────────────────────────────────────────────

export const newsArticleSchema = z
  .object({
    symbol: z.string(),
    headline: z.string(),
    summary: z.string().optional(),
    source: z.string().optional(),
    url: z.string().optional(),
    published_at: z.string().nullish(),
    sentiment: z.string().nullish(),
    sentiment_score: nullableNumber,
  })
  .passthrough()

export const newsFeedResponseSchema = z
  .object({
    total_articles: z.number(),
    returned: z.number(),
    articles: z.array(newsArticleSchema),
  })
  .passthrough()

export const sentimentSummarySchema = z
  .object({
    symbol: z.string(),
    buzz: z.number(),
    company_news_score: z.number(),
    sector_news_score: z.number(),
    article_count: z.number(),
    updated_at: z.string().nullish(),
    articles: z.array(newsArticleSchema),
  })
  .passthrough()

// ── Stocks ────────────────────────────────────────────────────────────────

export const stocksResponseSchema = z
  .object({
    total_in_database: z.number(),
    returned: z.number(),
    limit: z.number(),
    offset: z.number(),
    results: z.array(stockResultSchema),
  })
  .passthrough()
