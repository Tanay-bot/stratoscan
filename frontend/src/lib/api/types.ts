/**
 * TypeScript mirror of the Stratoscan FastAPI contract (src/api.py).
 * Field names and shapes match the backend responses exactly.
 */

// ── Screener ────────────────────────────────────────────────────────────────

export interface ScreenRequest {
  query: string
  limit?: number
  offset?: number
  sort_by?: string
  sort_desc?: boolean
}

export interface StockResult {
  symbol: string
  company_name: string
  sector?: string | null
  industry?: string | null
  current_price?: number | null
  price_change_pct?: number | null
  market_cap?: number | null
  pe_ratio?: number | null
  rsi_14?: number | null
  volume?: number | null
  dividend_yield?: number | null
  beta?: number | null
  sentiment?: {
    buzz: number
    company_news_score: number
    sector_news_score: number
    article_count: number
  } | null
  avg_volume?: number | null
  eps_ttm?: number | null
  sma_50?: number | null
  sma_200?: number | null
  high_52w?: number | null
  low_52w?: number | null
  pct_off_52w_high?: number | null
  ps_ratio?: number | null
  pb_ratio?: number | null
  ev_ebitda?: number | null
  roe_ttm?: number | null
  roa_ttm?: number | null
  gross_margin_ttm?: number | null
  net_margin_ttm?: number | null
  revenue_growth_ttm?: number | null
  eps_growth_ttm?: number | null
  macd_12_26?: number | null
  atr_14?: number | null
  bollinger_upper?: number | null
  bollinger_lower?: number | null
  score?: number | null
  score_components?: Record<string, number>
  why_matched?: string[]
  is_stale?: boolean
  data_age_minutes?: number | null
}

export interface ScreenResponse {
  query: string
  interpreted_filters: Record<string, unknown>
  total_matches: number
  results: StockResult[]
  limit: number
  offset: number
  has_more: boolean
  next_offset?: number | null
  sort_by?: string
  sort_desc?: boolean
  applied_filters?: string[]
  data_quality?: Record<string, unknown>
}

// ── Backtest ────────────────────────────────────────────────────────────────

export interface BacktestRequest {
  query: string
  top_n?: number
  /** Day 9: replaces the strategy's RSI entry threshold (or adds one). */
  rsi_buy_below?: number | null
  /** Day 9: replaces the strategy's exit with "RSI above N". */
  rsi_sell_above?: number | null
  stop_loss_pct?: number | null
  take_profit_pct?: number | null
  trailing_stop_pct?: number | null
  position_sizing?: 'all_in' | 'fixed_pct' | 'risk_based' | null
  position_pct?: number | null
  risk_per_trade_pct?: number | null
  allow_short?: boolean | null
  max_leverage?: number | null
  start_date?: string | null
  end_date?: string | null
  // Tier 5: portfolio simulation (shared pool + rebalance schedule)
  portfolio_allocation?: 'equal' | 'risk_based' | null
  rebalance_frequency?: 'none' | 'monthly' | 'quarterly' | 'yearly' | null
  // Tier 5: cash accounting (opt-in realism)
  dividend_reinvest?: boolean | null
  borrow_rate_pct?: number | null
  // Tier 5: diversification knob — max stocks per sector
  max_sector_exposure?: number | null
  // Tier 3 compare mode: pin the exact symbol set (strategy B re-runs on
  // strategy A's picks so the A/B is genuine). Skips the screener.
  symbols?: string[] | null
}

/** Tier 3: async backtest job status (polled from GET /backtest/async/{id}). */
export interface BacktestJobStatus {
  job_id: string
  status: 'queued' | 'running' | 'done' | 'error'
  phase?: string | null
  done_symbols?: string[]
  total_symbols?: number
  error?: string | null
  /** Present only when status === 'done' — identical to /backtest's payload. */
  result?: BacktestResponse | null
}

/** Response of POST /backtest/async and POST /try/backtest. */
export interface BacktestJobStarted {
  job_id: string
  status: string
  phase?: string
  total_symbols?: number
  /** Trial runs only: free backtests left after this one. */
  anon_remaining?: number
}

/** A public share link (POST /results/share, GET /results/shares). */
export interface ShareLink {
  share_id: string
  url: string
}

export interface ShareListItem extends ShareLink {
  query: string
  created_at: string
  headline: {
    total_return_pct: number | null
    benchmark_total_return_pct: number | null
  } | null
}

/** Day 10: a strategy the owner follows — re-run weekly and emailed to them. */
export interface SavedStrategy {
  id: number
  query: string
  symbols: string[]
  rules: { buy_rules: string[]; sell_rules: string[] }
  saved_return_pct: number | null
  saved_at: string
}

/** Day 11: a plain-English reading of one finished backtest (POST /explain). */
export interface ExplanationFinding {
  code: string
  tone: 'caution' | 'info' | 'positive'
  text: string
}

export interface Explanation {
  result_hash: string
  basis: 'single' | 'portfolio' | 'none'
  /** Deterministic, always present — computed from the result's own numbers. */
  findings: ExplanationFinding[]
  /** Model-written, shown only when it passed the compliance + number guards. */
  narrative: { summary: string; points: string[] } | null
  narrative_status: 'ok' | 'unavailable' | 'withheld'
  disclaimer: string
  cached: boolean
  facts: Record<string, unknown>
}

export interface EmailPreferences {
  weekly_digest: boolean
  email_verified: boolean
}

export interface PortfolioSummary {
  allocation: 'equal' | 'risk_based'
  rebalance: 'none' | 'monthly' | 'quarterly' | 'yearly'
  initial_capital: number
  final_value: number
  total_return_pct: number
  sharpe_ratio?: number | null
  max_drawdown_pct?: number | null
  weights: Record<string, number>
  equity_curve: { date: string; value: number }[]
  drawdown_curve?: { date: string; value: number }[]
  symbols: string[]
}

export interface TradeInfo {
  symbol: string
  buy_date: string
  buy_price: number
  sell_date?: string | null
  sell_price?: number | null
  shares: number
  pnl?: number | null
  pnl_pct?: number | null
  side: 'long' | 'short'
  exit_reason: string
}

export interface BacktestResultItem {
  symbol: string
  initial_capital: number
  final_value: number
  total_return_pct: number
  sharpe_ratio?: number | null
  max_drawdown_pct?: number | null
  win_rate_pct?: number | null
  total_trades: number
  trades: TradeInfo[]
  equity_curve: { date: string; value: number }[]
  drawdown_curve?: { date: string; value: number }[]
  warnings: string[]
  benchmark_total_return_pct?: number | null
  alpha_pct?: number | null
  beta?: number | null
  excess_return_pct?: number | null
  buy_hold_price_return_pct?: number | null
  buy_hold_total_return_pct?: number | null
  dividend_contribution_pct?: number | null
  data_quality?: string[]
  /** Tier 5 cash accounting (opt-in): $ received as dividends (negative = paid by shorts). */
  dividends_received?: number
  /** Tier 5 cash accounting (opt-in): $ paid as short borrow fees. */
  borrow_fees_paid?: number
  monte_carlo?: MonteCarloResult | null
  confidence_intervals?: ConfidenceIntervalsResult | null
  walk_forward?: WalkForwardResult | null
  parameter_sensitivity?: ParameterSensitivityResult | null
  /** Per-symbol data freshness from the screener (null = compare-mode or unknown). */
  data_age_minutes?: number | null
  is_stale?: boolean
}

/** Tier 3 robustness: trade-sequence bootstrap percentiles (fixed seed). */
export interface MonteCarloResult {
  available: boolean
  reason?: string | null
  simulations?: number
  trade_count?: number
  p5?: number
  p25?: number
  median?: number
  p75?: number
  p95?: number
  mean?: number
  prob_positive?: number
}

/** Tier 3 robustness: bootstrap CI on Sharpe + max drawdown. */
export interface ConfidenceIntervalsResult {
  available: boolean
  reason?: string | null
  samples?: number
  sharpe_ci?: [number, number]
  max_drawdown_ci?: [number, number]
  sharpe_t_stat?: number | null
  sharpe_sample_note?: string
}

/** Tier 3 robustness: walk-forward in-sample / out-of-sample + rolling windows. */
export interface WalkForwardResult {
  available: boolean
  reason?: string | null
  splits?: number
  in_sample?: {
    total_return: number
    sharpe_ratio: number | null
    max_drawdown: number
    total_trades: number
  }
  out_of_sample?: {
    total_return: number
    sharpe_ratio: number | null
    max_drawdown: number
    total_trades: number
  }
  rolling?: {
    windows: Array<{
      window_start: string
      window_end: string
      total_return: number
      sharpe_ratio: number | null
      max_drawdown: number
      total_trades: number
    }>
    median_return: number
    min_return: number
    max_return: number
    positive_windows: number
    total_windows: number
    verdict: string
  }
}

/** Tier 3 robustness: RSI × SMA parameter sweep grid. */
export interface ParameterSensitivityResult {
  available: boolean
  reason?: string | null
  grid?: Array<{
    rsi: number
    sma: number
    total_return: number
    sharpe_ratio: number | null
    max_drawdown: number
    total_trades: number
  }>
  best?: { rsi: number; sma: number; total_return: number }
  worst?: { rsi: number; sma: number; total_return: number }
  return_spread?: number
  losing_combos?: number
  total_combos?: number
  overfit_signal?: boolean
  verdict?: string
}

export interface BacktestResponse {
  query: string
  strategy: Record<string, unknown>
  results: Record<string, BacktestResultItem>
  total_symbols_tested: number
  /** Human-readable note when the run produced no results (e.g. 0 matches). */
  message?: string | null
  /** Tier 5: shared-pool portfolio aggregation (≥2 symbols). */
  portfolio?: PortfolioSummary | null
}

// ── Auth ────────────────────────────────────────────────────────────────────

export interface UserOut {
  id: number
  email: string
  plan: string
  email_verified: boolean
  is_admin?: boolean
  created_at?: string | null
}

export interface AuthResponse {
  access_token: string
  token_type: string
  expires_in: number
  email_sent?: boolean
  user: UserOut
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  password: string
}

// ── News ────────────────────────────────────────────────────────────────────

export interface NewsArticle {
  symbol: string
  headline: string
  summary: string
  source: string
  url: string
  published_at?: string | null
  sentiment?: 'bullish' | 'neutral' | 'bearish' | string | null
  sentiment_score?: number | null
}

export interface NewsFeedResponse {
  total_articles: number
  returned: number
  articles: NewsArticle[]
}

export interface SentimentSummary {
  symbol: string
  buzz: number
  company_news_score: number
  sector_news_score: number
  article_count: number
  updated_at?: string | null
  articles: NewsArticle[]
}

// ── Stocks ──────────────────────────────────────────────────────────────────

export interface StocksResponse {
  total_in_database: number
  returned: number
  limit: number
  offset: number
  results: StockResult[]
}

// ── API error shape (backend convention) ────────────────────────────────────

export interface ApiErrorDetail {
  error?: boolean
  detail?: string | unknown
  status_code?: number
  retry_after?: number
  message?: string
  verified?: boolean
  email?: string
}

export class ApiError extends Error {
  status: number
  retryAfter?: number

  constructor(message: string, status: number, retryAfter?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}
