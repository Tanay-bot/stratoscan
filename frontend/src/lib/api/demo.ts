/**
 * Demo-mode data engine.
 *
 * Serves realistic, deterministic synthetic data through the exact same
 * typed API interface as the real backend, so the UI is fully demoable
 * without a running Stratoscan server. Every value is generated with a
 * seeded PRNG (mulberry32) keyed by symbol, so prices and charts are
 * stable across reloads. The UI labels demo data as such.
 */
import type {
  BacktestRequest,
  BacktestResponse,
  BacktestResultItem,
  NewsArticle,
  NewsFeedResponse,
  ScreenRequest,
  ScreenResponse,
  SentimentSummary,
  StockResult,
  StocksResponse,
  UserOut,
} from './types'

// ── Seeded PRNG (mulberry32) ────────────────────────────────────────────────
function hashSeed(str: string): number {
  let h = 1779033703 ^ str.length
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353)
    h = (h << 13) | (h >>> 19)
  }
  return h >>> 0
}

function mulberry32(seed: number) {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function rngFor(seed: string) {
  return mulberry32(hashSeed(seed))
}

function pick<T>(rng: () => number, arr: T[]): T {
  return arr[Math.floor(rng() * arr.length)]
}

function between(rng: () => number, min: number, max: number): number {
  return min + rng() * (max - min)
}

// ── Stock universe (synthetic but plausible) ────────────────────────────────
interface Seed {
  symbol: string
  name: string
  sector: string
  industry: string
  price: number
  pe: number
  div: number
  beta: number
  cap: number // market cap in $
  momentum: 'high' | 'mid' | 'low'
  growth: number // revenue growth ttm
  margin: number // net margin ttm
}

const SEEDS: Seed[] = [
  {
    symbol: 'NVDA',
    name: 'NVIDIA Corp',
    sector: 'Technology',
    industry: 'Semiconductors',
    price: 168.4,
    pe: 52.1,
    div: 0.02,
    beta: 1.72,
    cap: 4110e9,
    momentum: 'high',
    growth: 0.62,
    margin: 0.49,
  },
  {
    symbol: 'AAPL',
    name: 'Apple Inc',
    sector: 'Technology',
    industry: 'Consumer Electronics',
    price: 232.9,
    pe: 35.4,
    div: 0.45,
    beta: 1.22,
    cap: 3530e9,
    momentum: 'mid',
    growth: 0.06,
    margin: 0.26,
  },
  {
    symbol: 'MSFT',
    name: 'Microsoft Corp',
    sector: 'Technology',
    industry: 'Software — Infrastructure',
    price: 452.2,
    pe: 38.7,
    div: 0.7,
    beta: 0.94,
    cap: 3360e9,
    momentum: 'mid',
    growth: 0.14,
    margin: 0.36,
  },
  {
    symbol: 'AMZN',
    name: 'Amazon.com Inc',
    sector: 'Consumer Cyclical',
    industry: 'Internet Retail',
    price: 221.3,
    pe: 41.2,
    div: 0,
    beta: 1.08,
    cap: 2330e9,
    momentum: 'mid',
    growth: 0.11,
    margin: 0.09,
  },
  {
    symbol: 'GOOGL',
    name: 'Alphabet Inc',
    sector: 'Communication Services',
    industry: 'Internet Content',
    price: 187.6,
    pe: 24.9,
    div: 0.45,
    beta: 1.03,
    cap: 2290e9,
    momentum: 'mid',
    growth: 0.14,
    margin: 0.28,
  },
  {
    symbol: 'META',
    name: 'Meta Platforms',
    sector: 'Communication Services',
    industry: 'Internet Content',
    price: 573.1,
    pe: 27.3,
    div: 0.4,
    beta: 1.2,
    cap: 1450e9,
    momentum: 'high',
    growth: 0.22,
    margin: 0.34,
  },
  {
    symbol: 'TSLA',
    name: 'Tesla Inc',
    sector: 'Consumer Cyclical',
    industry: 'Auto Manufacturers',
    price: 342.5,
    pe: 88.6,
    div: 0,
    beta: 2.31,
    cap: 1090e9,
    momentum: 'high',
    growth: 0.05,
    margin: 0.08,
  },
  {
    symbol: 'AVGO',
    name: 'Broadcom Inc',
    sector: 'Technology',
    industry: 'Semiconductors',
    price: 221.7,
    pe: 42.3,
    div: 1.2,
    beta: 1.18,
    cap: 1040e9,
    momentum: 'high',
    growth: 0.4,
    margin: 0.29,
  },
  {
    symbol: 'JPM',
    name: 'JPMorgan Chase',
    sector: 'Financial Services',
    industry: 'Banks — Diversified',
    price: 244.8,
    pe: 13.2,
    div: 2.1,
    beta: 1.09,
    cap: 690e9,
    momentum: 'mid',
    growth: 0.12,
    margin: 0.33,
  },
  {
    symbol: 'XOM',
    name: 'Exxon Mobil',
    sector: 'Energy',
    industry: 'Oil & Gas Integrated',
    price: 118.4,
    pe: 13.9,
    div: 3.3,
    beta: 0.84,
    cap: 520e9,
    momentum: 'low',
    growth: 0.04,
    margin: 0.11,
  },
  {
    symbol: 'CVX',
    name: 'Chevron Corp',
    sector: 'Energy',
    industry: 'Oil & Gas Integrated',
    price: 154.2,
    pe: 15.8,
    div: 4.2,
    beta: 1.05,
    cap: 283e9,
    momentum: 'low',
    growth: 0.03,
    margin: 0.12,
  },
  {
    symbol: 'COST',
    name: 'Costco Wholesale',
    sector: 'Consumer Defensive',
    industry: 'Discount Stores',
    price: 928.6,
    pe: 54.3,
    div: 0.5,
    beta: 0.76,
    cap: 412e9,
    momentum: 'mid',
    growth: 0.09,
    margin: 0.03,
  },
  {
    symbol: 'NFLX',
    name: 'Netflix Inc',
    sector: 'Communication Services',
    industry: 'Entertainment',
    price: 912.4,
    pe: 47.1,
    div: 0,
    beta: 1.31,
    cap: 392e9,
    momentum: 'high',
    growth: 0.16,
    margin: 0.22,
  },
  {
    symbol: 'ADBE',
    name: 'Adobe Inc',
    sector: 'Technology',
    industry: 'Software — Application',
    price: 448.9,
    pe: 34.2,
    div: 0,
    beta: 1.31,
    cap: 199e9,
    momentum: 'mid',
    growth: 0.1,
    margin: 0.3,
  },
  {
    symbol: 'CRM',
    name: 'Salesforce Inc',
    sector: 'Technology',
    industry: 'Software — Application',
    price: 312.7,
    pe: 47.8,
    div: 0.4,
    beta: 1.36,
    cap: 300e9,
    momentum: 'mid',
    growth: 0.1,
    margin: 0.14,
  },
  {
    symbol: 'ORCL',
    name: 'Oracle Corp',
    sector: 'Technology',
    industry: 'Software — Infrastructure',
    price: 178.3,
    pe: 41.5,
    div: 1.0,
    beta: 1.14,
    cap: 500e9,
    momentum: 'high',
    growth: 0.08,
    margin: 0.2,
  },
  {
    symbol: 'AMD',
    name: 'Advanced Micro Devices',
    sector: 'Technology',
    industry: 'Semiconductors',
    price: 158.2,
    pe: 71.4,
    div: 0,
    beta: 1.88,
    cap: 256e9,
    momentum: 'mid',
    growth: 0.22,
    margin: 0.07,
  },
  {
    symbol: 'INTC',
    name: 'Intel Corp',
    sector: 'Technology',
    industry: 'Semiconductors',
    price: 34.1,
    pe: 92.3,
    div: 1.4,
    beta: 1.09,
    cap: 147e9,
    momentum: 'low',
    growth: 0.01,
    margin: 0.02,
  },
  {
    symbol: 'PFE',
    name: 'Pfizer Inc',
    sector: 'Healthcare',
    industry: 'Drug Manufacturers',
    price: 26.8,
    pe: 18.4,
    div: 6.2,
    beta: 0.6,
    cap: 152e9,
    momentum: 'low',
    growth: 0.02,
    margin: 0.16,
  },
  {
    symbol: 'UNH',
    name: 'UnitedHealth Group',
    sector: 'Healthcare',
    industry: 'Healthcare Plans',
    price: 512.3,
    pe: 32.6,
    div: 1.6,
    beta: 0.62,
    cap: 472e9,
    momentum: 'low',
    growth: 0.08,
    margin: 0.06,
  },
  {
    symbol: 'LLY',
    name: 'Eli Lilly & Co',
    sector: 'Healthcare',
    industry: 'Drug Manufacturers',
    price: 902.8,
    pe: 88.4,
    div: 0.7,
    beta: 0.4,
    cap: 857e9,
    momentum: 'high',
    growth: 0.32,
    margin: 0.24,
  },
  {
    symbol: 'KO',
    name: 'Coca-Cola Co',
    sector: 'Consumer Defensive',
    industry: 'Beverages — Non-Alcoholic',
    price: 63.4,
    pe: 27.9,
    div: 3.1,
    beta: 0.58,
    cap: 273e9,
    momentum: 'low',
    growth: 0.06,
    margin: 0.23,
  },
  {
    symbol: 'PEP',
    name: 'PepsiCo Inc',
    sector: 'Consumer Defensive',
    industry: 'Beverages — Non-Alcoholic',
    price: 161.2,
    pe: 23.7,
    div: 3.4,
    beta: 0.53,
    cap: 221e9,
    momentum: 'low',
    growth: 0.02,
    margin: 0.14,
  },
  {
    symbol: 'DIS',
    name: 'Walt Disney Co',
    sector: 'Communication Services',
    industry: 'Entertainment',
    price: 112.8,
    pe: 41.2,
    div: 0.8,
    beta: 1.4,
    cap: 205e9,
    momentum: 'low',
    growth: 0.05,
    margin: 0.06,
  },
  {
    symbol: 'BA',
    name: 'Boeing Co',
    sector: 'Industrials',
    industry: 'Aerospace & Defense',
    price: 178.6,
    pe: 61.5,
    div: 0,
    beta: 1.51,
    cap: 133e9,
    momentum: 'low',
    growth: 0.08,
    margin: 0.01,
  },
  {
    symbol: 'CAT',
    name: 'Caterpillar Inc',
    sector: 'Industrials',
    industry: 'Farm & Heavy Machinery',
    price: 381.2,
    pe: 17.3,
    div: 1.6,
    beta: 1.12,
    cap: 185e9,
    momentum: 'mid',
    growth: 0.06,
    margin: 0.15,
  },
  {
    symbol: 'GE',
    name: 'GE Aerospace',
    sector: 'Industrials',
    industry: 'Aerospace & Defense',
    price: 187.9,
    pe: 31.4,
    div: 0.6,
    beta: 1.19,
    cap: 204e9,
    momentum: 'high',
    growth: 0.09,
    margin: 0.11,
  },
  {
    symbol: 'V',
    name: 'Visa Inc',
    sector: 'Financial Services',
    industry: 'Credit Services',
    price: 312.5,
    pe: 31.7,
    div: 0.75,
    beta: 0.97,
    cap: 616e9,
    momentum: 'mid',
    growth: 0.09,
    margin: 0.53,
  },
  {
    symbol: 'MA',
    name: 'Mastercard Inc',
    sector: 'Financial Services',
    industry: 'Credit Services',
    price: 528.3,
    pe: 38.2,
    div: 0.5,
    beta: 1.06,
    cap: 486e9,
    momentum: 'mid',
    growth: 0.12,
    margin: 0.55,
  },
  {
    symbol: 'BAC',
    name: 'Bank of America',
    sector: 'Financial Services',
    industry: 'Banks — Diversified',
    price: 46.2,
    pe: 13.8,
    div: 2.2,
    beta: 1.24,
    cap: 355e9,
    momentum: 'mid',
    growth: 0.07,
    margin: 0.3,
  },
  {
    symbol: 'WMT',
    name: 'Walmart Inc',
    sector: 'Consumer Defensive',
    industry: 'Discount Stores',
    price: 92.6,
    pe: 38.1,
    div: 0.9,
    beta: 0.53,
    cap: 745e9,
    momentum: 'mid',
    growth: 0.05,
    margin: 0.04,
  },
  {
    symbol: 'T',
    name: 'AT&T Inc',
    sector: 'Communication Services',
    industry: 'Telecom Services',
    price: 23.4,
    pe: 11.2,
    div: 4.6,
    beta: 0.68,
    cap: 168e9,
    momentum: 'low',
    growth: 0.02,
    margin: 0.1,
  },
  {
    symbol: 'VZ',
    name: 'Verizon Communications',
    sector: 'Communication Services',
    industry: 'Telecom Services',
    price: 41.8,
    pe: 9.4,
    div: 6.3,
    beta: 0.43,
    cap: 176e9,
    momentum: 'low',
    growth: 0.01,
    margin: 0.13,
  },
  {
    symbol: 'NKE',
    name: 'Nike Inc',
    sector: 'Consumer Cyclical',
    industry: 'Apparel Manufacturing',
    price: 78.4,
    pe: 22.6,
    div: 1.9,
    beta: 1.05,
    cap: 117e9,
    momentum: 'low',
    growth: 0.01,
    margin: 0.1,
  },
  {
    symbol: 'SBUX',
    name: 'Starbucks Corp',
    sector: 'Consumer Cyclical',
    industry: 'Restaurants',
    price: 98.2,
    pe: 28.4,
    div: 2.5,
    beta: 0.95,
    cap: 111e9,
    momentum: 'mid',
    growth: 0.04,
    margin: 0.12,
  },
  {
    symbol: 'GS',
    name: 'Goldman Sachs',
    sector: 'Financial Services',
    industry: 'Capital Markets',
    price: 612.4,
    pe: 16.8,
    div: 2.3,
    beta: 1.38,
    cap: 194e9,
    momentum: 'mid',
    growth: 0.12,
    margin: 0.22,
  },
  // ── Small / mid caps — so “small-cap tech” style queries have honest matches ──
  {
    symbol: 'SMCI',
    name: 'Super Micro Computer',
    sector: 'Technology',
    industry: 'Computer Hardware',
    price: 42.8,
    pe: 21.3,
    div: 0,
    beta: 2.1,
    cap: 2.5e9,
    momentum: 'mid',
    growth: 0.28,
    margin: 0.08,
  },
  {
    symbol: 'DOCU',
    name: 'DocuSign Inc',
    sector: 'Technology',
    industry: 'Software — Application',
    price: 74.2,
    pe: 24.8,
    div: 0,
    beta: 1.15,
    cap: 15.1e9,
    momentum: 'low',
    growth: 0.05,
    margin: 0.06,
  },
  {
    symbol: 'TWLO',
    name: 'Twilio Inc',
    sector: 'Technology',
    industry: 'Software — Infrastructure',
    price: 96.5,
    pe: 28.7,
    div: 0,
    beta: 1.34,
    cap: 14.8e9,
    momentum: 'mid',
    growth: 0.09,
    margin: 0.03,
  },
  {
    symbol: 'RBLX',
    name: 'Roblox Corp',
    sector: 'Technology',
    industry: 'Software — Application',
    price: 61.3,
    pe: 34.2,
    div: 0,
    beta: 1.52,
    cap: 38.2e9,
    momentum: 'high',
    growth: 0.24,
    margin: 0.02,
  },
  {
    symbol: 'DDOG',
    name: 'Datadog Inc',
    sector: 'Technology',
    industry: 'Software — Infrastructure',
    price: 148.6,
    pe: 42.5,
    div: 0,
    beta: 1.41,
    cap: 49.4e9,
    momentum: 'high',
    growth: 0.24,
    margin: 0.09,
  },
  {
    symbol: 'CRWD',
    name: 'CrowdStrike Holdings',
    sector: 'Technology',
    industry: 'Software — Infrastructure',
    price: 389.4,
    pe: 78.6,
    div: 0,
    beta: 1.38,
    cap: 94.5e9,
    momentum: 'high',
    growth: 0.31,
    margin: 0.08,
  },
  // True small caps (under $2B) so small-cap queries rank real small caps
  {
    symbol: 'BBAI',
    name: 'BigBear.ai Holdings',
    sector: 'Technology',
    industry: 'Software — Infrastructure',
    price: 4.62,
    pe: 18.9,
    div: 0,
    beta: 1.95,
    cap: 412e6,
    momentum: 'mid',
    growth: 0.18,
    margin: 0.02,
  },
  {
    symbol: 'SOUN',
    name: 'SoundHound AI',
    sector: 'Technology',
    industry: 'Software — Application',
    price: 11.84,
    pe: 22.4,
    div: 0,
    beta: 2.2,
    cap: 1.08e9,
    momentum: 'high',
    growth: 0.42,
    margin: 0.01,
  },
  {
    symbol: 'PNRG',
    name: 'PrimeEnergy Resources',
    sector: 'Energy',
    industry: 'Oil & Gas E&P',
    price: 142.7,
    pe: 8.4,
    div: 1.8,
    beta: 0.9,
    cap: 2.1e9,
    momentum: 'low',
    growth: 0.06,
    margin: 0.21,
  },
  {
    symbol: 'NRDY',
    name: 'Nerdy Inc',
    sector: 'Consumer Defensive',
    industry: 'Education',
    price: 1.24,
    pe: 12.1,
    div: 0,
    beta: 1.05,
    cap: 138e6,
    momentum: 'low',
    growth: 0.04,
    margin: 0.01,
  },
  {
    symbol: 'APPS',
    name: 'Digital Turbine',
    sector: 'Technology',
    industry: 'Software — Application',
    price: 2.31,
    pe: 9.8,
    div: 0,
    beta: 1.7,
    cap: 242e6,
    momentum: 'low',
    growth: 0.03,
    margin: 0.02,
  },
  {
    symbol: 'BTBT',
    name: 'Bit Digital',
    sector: 'Financial Services',
    industry: 'Capital Markets',
    price: 3.44,
    pe: 11.6,
    div: 0,
    beta: 2.6,
    cap: 412e6,
    momentum: 'mid',
    growth: 0.05,
    margin: 0.03,
  },
  {
    symbol: 'GRWG',
    name: 'GrowGeneration Corp',
    sector: 'Consumer Cyclical',
    industry: 'Specialty Retail',
    price: 1.87,
    pe: 7.2,
    div: 0,
    beta: 1.3,
    cap: 111e6,
    momentum: 'low',
    growth: 0.02,
    margin: 0.01,
  },
  {
    symbol: 'SLDB',
    name: 'Solid Biosciences',
    sector: 'Healthcare',
    industry: 'Biotechnology',
    price: 8.42,
    pe: 15.4,
    div: 0,
    beta: 1.1,
    cap: 312e6,
    momentum: 'mid',
    growth: 0.05,
    margin: 0.0,
  },
]

function buildStock(seed: Seed, rng: () => number): StockResult {
  const chg =
    (rng() - 0.45) * (seed.momentum === 'high' ? 4.5 : seed.momentum === 'mid' ? 2.4 : 1.4)
  const rsi =
    seed.momentum === 'high'
      ? between(rng, 55, 78)
      : seed.momentum === 'low'
        ? between(rng, 28, 45)
        : between(rng, 42, 58)
  const high52 = seed.price * (1 + between(rng, 0.06, 0.3))
  const low52 = seed.price * (1 - between(rng, 0.1, 0.42))
  const volume = Math.round(between(rng, 2e6, 9e7))
  const avgVolume = Math.round(volume * between(rng, 0.7, 1.2))
  const pctOffHigh = ((seed.price - high52) / high52) * 100

  const scoreComponents = {
    value: Math.round(between(rng, 20, 92)),
    momentum: Math.round(between(rng, 15, 95)),
    quality: Math.round(between(rng, 30, 88)),
  }
  const score = Math.round(
    scoreComponents.value * 0.3 + scoreComponents.momentum * 0.4 + scoreComponents.quality * 0.3,
  )

  const whyPool: Record<string, string[]> = {
    high: [
      'RSI above 55 with price above SMA-50',
      '52-week high within 8% — breakout zone',
      'Revenue growth accelerating 2 quarters straight',
      'Composite score in top decile',
    ],
    mid: ['Price holding above SMA-200', 'Beta near market', 'Stable revenue growth trajectory'],
    low: [
      'RSI below 45 — oversold territory',
      'Dividend yield above sector median',
      'Price near 52-week low — value setup',
    ],
  }

  const sentimentScore = clampDemo(rng() * 2 - 1, -1, 1)

  return {
    symbol: seed.symbol,
    company_name: seed.name,
    sector: seed.sector,
    industry: seed.industry,
    current_price: round2(seed.price * (1 + chg / 100)),
    price_change_pct: round2(chg),
    market_cap: Math.round(seed.cap * (1 + between(rng, -0.05, 0.05))),
    pe_ratio: round2(seed.pe * between(rng, 0.9, 1.12)),
    rsi_14: round1(rsi),
    volume,
    dividend_yield: round2(seed.div * between(rng, 0.85, 1.15)),
    beta: round2(seed.beta * between(rng, 0.9, 1.1)),
    sentiment: {
      buzz: round2(between(rng, 0.2, 1)),
      company_news_score: round2(sentimentScore),
      sector_news_score: round2(between(rng, -0.6, 0.8)),
      article_count: Math.round(between(rng, 3, 28)),
    },
    avg_volume: avgVolume,
    eps_ttm: round2(seed.price / seed.pe),
    sma_50: round2(seed.price * (1 + between(rng, -0.12, 0.1))),
    sma_200: round2(seed.price * (1 + between(rng, -0.25, 0.08))),
    high_52w: round2(high52),
    low_52w: round2(low52),
    pct_off_52w_high: round2(pctOffHigh),
    ps_ratio: round2(between(rng, 0.8, 12)),
    pb_ratio: round2(between(rng, 1.2, 22)),
    ev_ebitda: round2(between(rng, 6, 32)),
    roe_ttm: round1(seed.margin * between(rng, 0.5, 1.4) * 100),
    roa_ttm: round1(seed.margin * between(rng, 0.3, 0.9) * 100),
    gross_margin_ttm: round1(between(rng, 18, 78)),
    net_margin_ttm: round1(seed.margin * 100),
    revenue_growth_ttm: round2(seed.growth * between(rng, 0.8, 1.2) * 100),
    eps_growth_ttm: round2(seed.growth * between(rng, 0.9, 1.4) * 100),
    macd_12_26: round2(between(rng, -2, 4)),
    atr_14: round2(seed.price * between(rng, 0.008, 0.03)),
    bollinger_upper: round2(seed.price * (1 + between(rng, 0.02, 0.06))),
    bollinger_lower: round2(seed.price * (1 - between(rng, 0.02, 0.06))),
    score,
    score_components: scoreComponents,
    why_matched: whyPool[seed.momentum],
    is_stale: false,
    data_age_minutes: Math.round(between(rng, 1, 55)),
  }
}

function clampDemo(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n))
}
function round2(n: number) {
  return Math.round(n * 100) / 100
}
function round1(n: number) {
  return Math.round(n * 10) / 10
}

const STOCK_CACHE = new Map<string, StockResult>()
const ALL_STOCKS: StockResult[] = SEEDS.map((s) => {
  const rng = rngFor(`stock:${s.symbol}`)
  const stock = buildStock(s, rng)
  STOCK_CACHE.set(stock.symbol, stock)
  return stock
})

// ── Price history (for charts) ──────────────────────────────────────────────
export function priceHistory(symbol: string, points = 250): { date: string; value: number }[] {
  const rng = rngFor(`history:${symbol}`)
  const stock = STOCK_CACHE.get(symbol) ?? ALL_STOCKS[0]
  const base = (stock.current_price ?? 100) / (1 + between(rng, 0.05, 0.4))
  const drift = (rng() - 0.42) * 0.0009 // slight upward bias
  const vol = between(rng, 0.012, 0.03)
  const out: { date: string; value: number }[] = []
  let value = base
  const now = Date.now()
  for (let i = points; i >= 1; i--) {
    // add momentum-ish regime changes
    const regime = Math.sin(i / 23) * 0.004 + between(rng, -1, 1) * vol * 0.4
    value = value * (1 + drift + regime)
    const d = new Date(now - i * 86_400_000)
    out.push({ date: d.toISOString().slice(0, 10), value: round2(value) })
  }
  return out
}

export function drawdownCurve(
  equity: { date: string; value: number }[],
): { date: string; value: number }[] {
  let peak = -Infinity
  return equity.map((p) => {
    peak = Math.max(peak, p.value)
    return { date: p.date, value: round2((p.value - peak) / peak) }
  })
}

// ── Screen ──────────────────────────────────────────────────────────────────
const SCREEN_RULES: Array<{ re: RegExp; label: string; pred: (s: StockResult) => boolean }> = [
  {
    re: /small-?cap|small cap/i,
    label: 'market cap between $300M and $2B',
    pred: (s) => (s.market_cap ?? 0) < 2e9 && (s.market_cap ?? 0) > 300e6,
  },
  {
    re: /large-?cap|large cap|mega/i,
    label: 'large-cap (>$10B market cap)',
    pred: (s) => (s.market_cap ?? 0) > 10e9,
  },
  { re: /tech/i, label: 'sector = Technology', pred: (s) => s.sector === 'Technology' },
  { re: /energy/i, label: 'sector = Energy', pred: (s) => s.sector === 'Energy' },
  {
    re: /health|biotech|pharma/i,
    label: 'sector = Healthcare',
    pred: (s) => s.sector === 'Healthcare',
  },
  {
    re: /financial|bank/i,
    label: 'sector = Financial Services',
    pred: (s) => s.sector === 'Financial Services',
  },
  {
    re: /consumer|retail/i,
    label: 'sector = Consumer',
    pred: (s) => s.sector?.startsWith('Consumer') ?? false,
  },
  { re: /industri/i, label: 'sector = Industrials', pred: (s) => s.sector === 'Industrials' },
  {
    re: /dividend|income/i,
    label: 'dividend yield ≥ 3%',
    pred: (s) => (s.dividend_yield ?? 0) >= 3,
  },
  {
    re: /momentum|breakout|rising|uptrend/i,
    label: 'momentum: RSI ≥ 55',
    pred: (s) => (s.rsi_14 ?? 0) >= 55,
  },
  {
    re: /oversold|dip|undervalued buy/i,
    label: 'momentum: RSI ≤ 45',
    pred: (s) => (s.rsi_14 ?? 0) <= 45,
  },
  {
    re: /undervalued|cheap|value/i,
    label: 'value: PE below sector median',
    pred: (s) => (s.pe_ratio ?? 999) < 25,
  },
  {
    re: /high.?volume/i,
    label: 'volume above average',
    pred: (s) => (s.volume ?? 0) > (s.avg_volume ?? 0),
  },
  { re: /low.?beta/i, label: 'beta < 1', pred: (s) => (s.beta ?? 9) < 1 },
  { re: /growth/i, label: 'revenue growth > 10%', pred: (s) => (s.revenue_growth_ttm ?? 0) > 10 },
  {
    re: /low.?risk|defensive/i,
    label: 'low beta + dividend',
    pred: (s) => (s.beta ?? 9) < 0.8 && (s.dividend_yield ?? 0) > 2,
  },
]

export function demoScreen(req: ScreenRequest): ScreenResponse {
  const rules = SCREEN_RULES.filter((r) => r.re.test(req.query))
  const applied: string[] = rules.map((r) => r.label)

  // Rank by how many extracted rules each stock satisfies (soft matching).
  // A "small-cap tech" query must never surface NVDA just because it matches
  // the tech rule alone — stocks that satisfy MORE of the query rank first.
  let pool: StockResult[]
  if (rules.length) {
    const scored = ALL_STOCKS.map((s) => ({
      s,
      hits: rules.reduce((n, r) => n + (r.pred(s) ? 1 : 0), 0),
    }))
    const maxHits = Math.max(0, ...scored.map((x) => x.hits))
    pool = maxHits > 0 ? scored.filter((x) => x.hits === maxHits).map((x) => x.s) : []
  } else {
    pool = ALL_STOCKS
  }
  // If nothing matches any rule, fall back to a sensible ranking so the demo is never empty
  if (!pool.length)
    pool = [...ALL_STOCKS].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 8)

  // sort
  const sortBy = req.sort_by ?? 'market_cap'
  const desc = req.sort_desc ?? true
  const sorted = [...pool].sort((a, b) => {
    const av = a[sortBy as keyof StockResult] ?? 0
    const bv = b[sortBy as keyof StockResult] ?? 0
    if (typeof av === 'number' && typeof bv === 'number') return desc ? bv - av : av - bv
    return String(av).localeCompare(String(bv))
  })

  const limit = req.limit ?? 50
  const offset = req.offset ?? 0
  const page = sorted.slice(offset, offset + limit)

  const results = page.map((s) => ({
    ...s,
    score: s.score,
    why_matched: rules.length ? s.why_matched : undefined,
  }))

  return {
    query: req.query,
    interpreted_filters: {
      ...(applied.length ? {} : { note: 'No specific filters extracted' }),
      ...Object.fromEntries(applied.map((a) => [a, true])),
    },
    total_matches: sorted.length,
    results,
    limit,
    offset,
    has_more: offset + limit < sorted.length,
    next_offset: offset + limit < sorted.length ? offset + limit : null,
    sort_by: sortBy,
    sort_desc: desc,
    applied_filters: applied,
    data_quality: { coverage_pct: 96, stale_pct: 0, demo: true },
  }
}

// ── Backtest ────────────────────────────────────────────────────────────────
function equityFor(
  symbol: string,
  startDate: string | null,
  endDate: string | null,
  rng: () => number,
) {
  const raw = priceHistory(symbol, 1200)
  const start = startDate ? new Date(startDate + 'T00:00:00Z').getTime() : null
  const end = endDate ? new Date(endDate + 'T00:00:00Z').getTime() : null
  const filtered = raw.filter((p) => {
    const t = new Date(p.date + 'T00:00:00Z').getTime()
    return (!start || t >= start) && (!end || t <= end)
  })
  if (filtered.length < 60) {
    return raw.slice(-252).map((p) => ({ ...p, value: p.value }))
  }
  // Build an equity curve from the price path with plausible strategy alpha
  const first = filtered[0].value
  const alpha = (rng() - 0.3) * 0.0006
  return filtered.map((p, i) => ({
    date: p.date,
    value: round2(first * (1 + (p.value / first - 1) * (1.4 + i * alpha))),
  }))
}

export function demoBacktest(req: BacktestRequest): BacktestResponse {
  const rng = rngFor(`bt:${req.query.toLowerCase()}:${req.top_n ?? 5}`)
  const interpreted = SCREEN_RULES.filter((r) => r.re.test(req.query))
  let pool = ALL_STOCKS
  if (interpreted.length) {
    pool = ALL_STOCKS.filter((s) => interpreted.some((r) => r.pred(s)))
  }
  if (!pool.length)
    pool = [...ALL_STOCKS].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 6)
  const topN = Math.min(req.top_n ?? 5, pool.length)
  const symbols = pool.slice(0, topN).map((s) => s.symbol)

  const stopLoss = req.stop_loss_pct != null ? req.stop_loss_pct / 100 : between(rng, 0.06, 0.12)
  const takeProfit =
    req.take_profit_pct != null ? req.take_profit_pct / 100 : between(rng, 0.12, 0.3)
  const sizing = req.position_sizing ?? 'fixed_pct'
  const initialCapital = 100_000

  const results: Record<string, BacktestResultItem> = {}
  for (const sym of symbols) {
    const equity = equityFor(sym, req.start_date ?? null, req.end_date ?? null, rng)
    const startV = equity[0].value
    const endV = equity[equity.length - 1].value
    const totalReturn = (endV / startV - 1) * 100
    const benchReturn = totalReturn * between(rng, 0.35, 0.75) - between(rng, 2, 14)
    const winRate = clampDemo(between(rng, 0.38, 0.74), 0, 1) * 100
    const totalTrades = Math.max(2, Math.round(between(rng, 4, 24)))
    const maxDD = -Math.abs(between(rng, 6, 26))
    const sharpe = clampDemo(between(rng, -0.2, 1.9), -1.5, 3.5)

    // Generate trades along the equity path
    const trades = []
    const priceSeries = priceHistory(sym, totalTrades * 3)
    let j = 0
    for (let i = 0; i < totalTrades; i++) {
      const buyIdx = j
      const buyPrice = priceSeries[buyIdx]?.value ?? 100
      const sellIdx = Math.min(buyIdx + 12 + Math.floor(rng() * 30), priceSeries.length - 1)
      const sellPrice = priceSeries[sellIdx]?.value ?? 105
      const side: 'long' | 'short' = rng() > 0.85 ? 'short' : 'long'
      const shares = Math.max(1, Math.round((initialCapital * 0.2) / buyPrice))
      const pnlPct = ((sellPrice - buyPrice) / buyPrice) * 100 * (side === 'long' ? 1 : -1)
      const exitReasons = ['signal', 'take_profit', 'stop_loss', 'trailing_stop']
      trades.push({
        symbol: sym,
        buy_date: priceSeries[buyIdx]?.date ?? '2024-01-05',
        buy_price: round2(buyPrice),
        sell_date: priceSeries[sellIdx]?.date ?? '2024-06-05',
        sell_price: round2(sellPrice),
        shares,
        pnl: round2(shares * (sellPrice - buyPrice) * (side === 'long' ? 1 : -1)),
        pnl_pct: round2(pnlPct),
        side,
        exit_reason: exitReasons[Math.floor(rng() * exitReasons.length)],
      })
      j = sellIdx + 1
    }

    results[sym] = {
      symbol: sym,
      initial_capital: initialCapital,
      final_value: round2(initialCapital * (1 + totalReturn / 100)),
      total_return_pct: round2(totalReturn),
      sharpe_ratio: round2(sharpe),
      max_drawdown_pct: round2(maxDD),
      win_rate_pct: round1(winRate),
      total_trades: totalTrades,
      trades,
      equity_curve: equity,
      drawdown_curve: drawdownCurve(equity),
      warnings: sharpe < 0.5 ? ['Sharpe below 0.5 — consider tighter risk controls'] : [],
      benchmark_total_return_pct: round2(benchReturn),
      alpha_pct: round2(totalReturn - benchReturn),
      beta: round2(between(rng, 0.6, 1.6)),
      excess_return_pct: round2(totalReturn - benchReturn),
      buy_hold_price_return_pct: round2(totalReturn * 0.8),
      buy_hold_total_return_pct: round2(totalReturn * 0.82),
      dividend_contribution_pct: round2(between(rng, 0.1, 1.8)),
      data_quality: ['yfinance history: 10y (verified)', 'splits/dividends adjusted'],
      monte_carlo: {
        available: true,
        simulations: 500,
        trade_count: 18,
        p5: round2(totalReturn * 0.42),
        p25: round2(totalReturn * 0.8),
        median: round2(totalReturn * 0.94),
        p75: round2(totalReturn * 1.2),
        p95: round2(totalReturn * 1.5),
        mean: round2(totalReturn * 0.98),
        prob_positive: between(rng, 0.72, 0.94),
      },
      confidence_intervals: {
        available: true,
        samples: 500,
        sharpe_ci: [round2(sharpe - 0.45), round2(sharpe + 0.45)],
        max_drawdown_ci: [round2(maxDD - 6), round2(maxDD + 3)],
        sharpe_t_stat: round2(sharpe * 1.4),
        sharpe_sample_note:
          '500 daily observations — a Sharpe t-stat below ~2 means the edge is not statistically distinguishable from noise.',
      },
      walk_forward: {
        available: true,
        splits: 4,
        in_sample: {
          total_return: round2(totalReturn * 0.9),
          sharpe_ratio: round2(sharpe * 1.1),
          max_drawdown: round2(maxDD * 0.9),
          total_trades: 14,
        },
        out_of_sample: {
          total_return: round2(totalReturn * 0.7),
          sharpe_ratio: round2(sharpe * 0.8),
          max_drawdown: round2(maxDD * 1.1),
          total_trades: 11,
        },
        rolling: {
          windows: [0.04, 0.08, -0.02, 0.06].map((w, i) => ({
            window_start: `202${i}-01-01`,
            window_end: `202${i + 1}-12-31`,
            total_return: w,
            sharpe_ratio: round2(sharpe * (1 + w)),
            max_drawdown: round2(maxDD * 0.9),
            total_trades: 8,
          })),
          median_return: round2(0.05),
          min_return: round2(-0.02),
          max_return: round2(0.08),
          positive_windows: 3,
          total_windows: 4,
          verdict: 'Mixed (3/4 windows positive)',
        },
      },
      parameter_sensitivity: {
        available: true,
        grid: [20, 25, 30, 35].flatMap((rsi) =>
          [30, 50, 70].map((sma) => ({
            rsi,
            sma,
            total_return: round2(totalReturn * between(rng, 0.6, 1.3)),
            sharpe_ratio: round2(sharpe * between(rng, 0.7, 1.2)),
            max_drawdown: round2(maxDD * 0.9),
            total_trades: 9,
          })),
        ),
        best: { rsi: 30, sma: 50, total_return: round2(totalReturn * 1.2) },
        worst: { rsi: 20, sma: 70, total_return: round2(totalReturn * 0.6) },
        return_spread: round2(totalReturn * 0.6),
        losing_combos: 2,
        total_combos: 12,
        overfit_signal: false,
        verdict: 'Robust: return is stable across the parameter grid.',
      },
    }
  }

  return {
    query: req.query,
    strategy: {
      entry: interpreted.map((r) => r.label).join(' AND ') || 'Composite score > 70',
      exit: `stop_loss ${Math.round(stopLoss * 100)}% / take_profit ${Math.round(takeProfit * 100)}%`,
      position_sizing: sizing,
      initial_capital: initialCapital,
      fees: '0.1% per side + slippage 5bps',
    },
    results,
    total_symbols_tested: topN,
  }
}

// ── News ────────────────────────────────────────────────────────────────────
const SOURCES = [
  'MarketWire',
  'Global Equities',
  'StreetSignals',
  'Bloomberg Terminal',
  'FinPress',
  'InvestorDaily',
  'Reuters Flow',
]
const HEADLINES: Array<[string, string, number]> = [
  [
    'beats quarterly earnings estimates, raises guidance',
    'revenue grew faster than analyst consensus while margins expanded',
    0.8,
  ],
  [
    'announces $10B share buyback program',
    'board authorized a new repurchase plan effective immediately',
    0.6,
  ],
  [
    'upgraded by analysts on improving fundamentals',
    'two sell-side firms raised price targets citing better visibility',
    0.55,
  ],
  [
    'warns of softer demand into next quarter',
    'management flagged a deceleration in order flow',
    -0.6,
  ],
  [
    'settles regulatory dispute, removes overhang',
    'the resolution clears a multi-year uncertainty for shareholders',
    0.5,
  ],
  [
    'expands into new market with strategic partnership',
    'the deal opens a faster-growing addressable market',
    0.7,
  ],
  [
    'misses revenue expectations, stock slips',
    'the shortfall was driven by one-time charges and FX headwinds',
    -0.7,
  ],
  [
    'insider buying detected among senior executives',
    'a cluster of open-market purchases was reported this week',
    0.4,
  ],
  [
    'announces dividend increase and capital return',
    'payout rises alongside a continued buyback program',
    0.65,
  ],
  [
    'faces margin pressure from rising input costs',
    'analysts trimmed estimates on near-term profitability',
    -0.45,
  ],
  [
    'launches next-generation product line',
    'early reviews suggest strong commercial traction',
    0.75,
  ],
  [
    'cuts full-year outlook citing macro headwinds',
    'the guidance reduction surprised the street',
    -0.8,
  ],
]

export function demoNews(symbol?: string, limit = 60): NewsFeedResponse {
  const universe = symbol ? [symbol] : ALL_STOCKS.map((s) => s.symbol)
  const count = symbol ? Math.min(12, limit) : Math.min(limit, 40)
  const articles: NewsArticle[] = []

  for (let i = 0; i < count; i++) {
    // Independent seed per article so a feed never shows repeated headlines
    const rng = rngFor(`news:${symbol ?? 'feed'}:${i}`)
    const sym = pick(rng, universe)
    const [tail, summary, score] = pick(rng, HEADLINES)
    const sentiment = score > 0.25 ? 'bullish' : score < -0.25 ? 'bearish' : 'neutral'
    const hoursAgo = Math.floor(between(rng, 0.5, 72))
    const d = new Date(Date.now() - hoursAgo * 3_600_000)
    const company = STOCK_CACHE.get(sym)?.company_name ?? sym
    articles.push({
      symbol: sym,
      headline: `${company} ${tail}`,
      summary: `${company}: ${summary}. The development was covered across ${pick(rng, SOURCES)} and market commentary desks.`,
      source: pick(rng, SOURCES),
      url: `https://example.com/news/${sym.toLowerCase()}-${i}`,
      published_at: d.toISOString(),
      sentiment,
      sentiment_score: round2(score),
    })
  }

  articles.sort((a, b) => (b.published_at ?? '').localeCompare(a.published_at ?? ''))
  return { total_articles: articles.length, returned: articles.length, articles }
}

export function demoSentimentSummary(symbol: string): SentimentSummary {
  const rng = rngFor(`sent:${symbol}`)
  const feed = demoNews(symbol, 12)
  const bullish = feed.articles.filter((a) => a.sentiment === 'bullish').length
  const bearish = feed.articles.filter((a) => a.sentiment === 'bearish').length
  return {
    symbol,
    buzz: round2(between(rng, 0.2, 0.95)),
    company_news_score: round2((bullish - bearish) / Math.max(1, feed.articles.length)),
    sector_news_score: round2(between(rng, -0.5, 0.7)),
    article_count: feed.articles.length,
    updated_at: new Date().toISOString(),
    articles: feed.articles,
  }
}

// ── Stocks list ─────────────────────────────────────────────────────────────
export function demoStocks(limit = 50, offset = 0): StocksResponse {
  const page = ALL_STOCKS.slice(offset, offset + limit)
  return {
    total_in_database: ALL_STOCKS.length,
    returned: page.length,
    limit,
    offset,
    results: page,
  }
}

export function demoStockBySymbol(symbol: string): StockResult | null {
  return STOCK_CACHE.get(symbol.toUpperCase()) ?? null
}

export { ALL_STOCKS }

// ── Demo auth ───────────────────────────────────────────────────────────────
export function demoLogin(
  email: string,
  password: string,
): { access_token: string; user: UserOut } {
  if (password.length < 8) {
    throw new Error('Password must be at least 8 characters.')
  }
  return {
    access_token: `demo.${btoa(email)}.${Date.now()}`,
    user: {
      id: 1,
      email: email.toLowerCase(),
      plan: 'free',
      email_verified: true,
      is_admin: false,
      created_at: new Date().toISOString(),
    },
  }
}
