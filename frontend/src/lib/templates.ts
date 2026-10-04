/**
 * The strategy template library (Day 8: nobody faces an empty box).
 *
 * Mirror of `stratoscan/src/strategy_templates.py` — the backend is the
 * source of truth and `tests/test_strategy_templates.py` fails if the ids or
 * queries here drift from it. Kept as a static copy (not fetched from /meta)
 * so /strategies renders instantly, in demo mode, and for crawlers.
 *
 * Every template parses on the free deterministic path, trades exactly the
 * rules in `simulates`, and holds positions for weeks rather than a day.
 */

export interface StrategyTemplate {
  id: string
  title: string
  query: string
  /** What the template tests — one line. */
  idea: string
  category: string
  /** Plain-English account of the exact rules the engine trades. */
  simulates: string
}

export const TEMPLATES: readonly StrategyTemplate[] = [
  {
    id: 'rsi-pullback-uptrend',
    title: 'RSI pullback in an uptrend',
    query: 'large cap stocks with RSI below 40 trading above their 200-day moving average',
    idea: 'Buying short dips inside a long-term uptrend beats chasing strength.',
    category: 'Trend + timing',
    simulates: 'Buys when RSI(14) drops below 40; sells when RSI(14) rises above 70.',
  },
  {
    id: 'oversold-mean-reversion',
    title: 'Oversold mean reversion',
    query: 'oversold large cap stocks with RSI below 30',
    idea: 'Deeply oversold large caps tend to bounce.',
    category: 'Mean reversion',
    simulates: 'Buys when RSI(14) drops below 30; sells when RSI(14) rises above 70.',
  },
  {
    id: 'low-volatility-value',
    title: 'Low-volatility value',
    query: 'large cap low beta value stocks with P/E below 15 and RSI below 35',
    idea: 'Cheap, steady stocks bought on a dip hold up on a risk-adjusted basis.',
    category: 'Value',
    simulates: 'Buys when RSI(14) drops below 35; sells when RSI(14) rises above 70.',
  },
  {
    id: 'quality-reasonable-price',
    title: 'Quality at a reasonable price',
    query: 'profitable large cap stocks with ROE above 15% and P/E below 20 and RSI below 35',
    idea: 'Profitability plus a sane multiple beats either one alone.',
    category: 'Quality',
    simulates: 'Buys when RSI(14) drops below 35; sells when RSI(14) rises above 70.',
  },
  {
    id: 'beaten-down-quality',
    title: 'Beaten-down quality',
    query: 'profitable large cap stocks near their 52-week low with ROE above 15% and RSI below 30',
    idea: 'Good businesses near a 52-week low recover.',
    category: 'Quality',
    simulates: 'Buys when RSI(14) drops below 30; sells when RSI(14) rises above 70.',
  },
  {
    id: 'dividend-durability',
    title: 'Dividend durability',
    query: 'large cap stocks with dividend yield above 3% and beta below 1 and RSI below 35',
    idea: 'Steady dividend payers hold up through drawdowns.',
    category: 'Income',
    simulates: 'Buys when RSI(14) drops below 35; sells when RSI(14) rises above 70.',
  },
  {
    id: 'utility-income-dip',
    title: 'Utility income on a dip',
    query: 'utilities with dividend yield above 3% and RSI below 35',
    idea: 'High-yield utilities bought when oversold pay you to wait.',
    category: 'Income',
    simulates: 'Buys when RSI(14) drops below 35; sells when RSI(14) rises above 70.',
  },
  {
    id: 'mega-cap-defensive',
    title: 'Mega-cap defensive',
    query: 'mega cap stocks with beta below 0.8 and RSI below 40',
    idea: 'Size and stability cushion the worst quarters.',
    category: 'Defensive',
    simulates: 'Buys when RSI(14) drops below 40; sells when RSI(14) rises above 70.',
  },
  {
    id: 'tech-pullback',
    title: 'Tech pullback',
    query: 'technology stocks with RSI below 40 trading above their 200-day moving average',
    idea: 'Tech leaders in an uptrend reward buying the dip.',
    category: 'Sector',
    simulates: 'Buys when RSI(14) drops below 40; sells when RSI(14) rises above 70.',
  },
  {
    id: 'energy-pullback',
    title: 'Energy pullback',
    query: 'energy stocks with RSI below 40 trading above their 200-day moving average',
    idea: 'Dips in an energy uptrend are entries, not exits.',
    category: 'Sector',
    simulates: 'Buys when RSI(14) drops below 40; sells when RSI(14) rises above 70.',
  },
  {
    id: 'healthcare-oversold',
    title: 'Oversold healthcare',
    query: 'oversold healthcare stocks with RSI below 35',
    idea: 'Defensive healthcare names snap back from oversold.',
    category: 'Sector',
    simulates: 'Buys when RSI(14) drops below 35; sells when RSI(14) rises above 70.',
  },
  {
    id: 'financials-oversold',
    title: 'Oversold financials',
    query: 'oversold financial stocks with RSI below 35',
    idea: 'Banks and insurers overshoot on bad news, then revert.',
    category: 'Sector',
    simulates: 'Buys when RSI(14) drops below 35; sells when RSI(14) rises above 70.',
  },
]

/** A small, varied starter set for empty states (one per idea family). */
export const STARTER_TEMPLATE_IDS = [
  'rsi-pullback-uptrend',
  'oversold-mean-reversion',
  'quality-reasonable-price',
  'dividend-durability',
] as const

export function templateById(id: string): StrategyTemplate | undefined {
  return TEMPLATES.find((t) => t.id === id)
}
