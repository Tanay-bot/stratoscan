/**
 * The engine's strategy echo, in plain English (Day 9, layer 3).
 *
 * `BacktestResponse.strategy` is the exact configuration the engine ran:
 * `buy_rules` / `sell_rules` strings ("rsi < 35", "price > sma_50") plus the
 * execution and risk parameters. This module renders it for people and marks
 * which values are the engine's defaults, so nobody thinks a default was a
 * choice someone made for them.
 */

const NAMES: Record<string, string> = {
  rsi: 'RSI(14)',
  price: 'price',
  volume: 'volume',
  sma_30: 'the 30-day average',
  sma_50: 'the 50-day average',
  sma_70: 'the 70-day average',
  sma_200: 'the 200-day average',
}

const OPS: Record<string, string> = {
  '<': 'below',
  '>': 'above',
  '<=': 'at or below',
  '>=': 'at or above',
  '==': 'equal to',
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** "rsi < 35" → "RSI(14) below 35"; "price > sma_50" → "Price above the 50-day average". */
export function describeRule(rule: string): string {
  const parts = rule.trim().split(/\s+/)
  if (parts.length !== 3) return rule
  const [field, op, rhs] = parts
  if (!(op in OPS)) return rule
  const lhs = NAMES[field] ?? field
  const num = Number(rhs)
  const right = Number.isFinite(num) ? String(num) : (NAMES[rhs] ?? rhs)
  return cap(`${lhs} ${OPS[op]} ${right}`)
}

/** The RSI threshold inside a rule list, if there is exactly that kind of rule. */
export function rsiThreshold(rules: string[], op: '<' | '>'): number | null {
  for (const r of rules) {
    const m = r.trim().match(/^rsi\s*([<>])\s*(-?\d+(?:\.\d+)?)$/)
    if (m && m[1] === op) return Number(m[2])
  }
  return null
}

export interface Param {
  label: string
  value: string
  /** True when this is the engine's default, not something anyone chose. */
  isDefault: boolean
}

const pct = (v: number) => `${+(v * 100).toFixed(3)}%`

/** Engine defaults (stratoscan/src/backtester.py DEFAULT_*). */
export const ENGINE_DEFAULTS = {
  fee_rate: 0.001,
  slippage: 0.0005,
  position_sizing: 'all_in',
  max_leverage: 1,
} as const

export interface StrategyView {
  entry: string[]
  exit: string[]
  params: Param[]
}

/** Render the `strategy` echo. Unknown or malformed input degrades to empty lists. */
export function describeStrategy(
  strategy: Record<string, unknown> | null | undefined,
): StrategyView {
  const s = strategy ?? {}
  const list = (v: unknown) =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const params: Param[] = []

  const fee = num(s.fee_rate)
  if (fee !== null)
    params.push({
      label: 'Fee per trade',
      value: pct(fee),
      isDefault: fee === ENGINE_DEFAULTS.fee_rate,
    })
  const slip = num(s.slippage)
  if (slip !== null)
    params.push({
      label: 'Slippage per trade',
      value: pct(slip),
      isDefault: slip === ENGINE_DEFAULTS.slippage,
    })
  const stop = num(s.stop_loss)
  params.push({ label: 'Stop loss', value: stop ? pct(stop) : 'Off', isDefault: !stop })
  const target = num(s.take_profit)
  params.push({ label: 'Take profit', value: target ? pct(target) : 'Off', isDefault: !target })
  const trail = num(s.trailing_stop)
  params.push({ label: 'Trailing stop', value: trail ? pct(trail) : 'Off', isDefault: !trail })
  const sizing = typeof s.position_sizing === 'string' ? s.position_sizing : null
  if (sizing) {
    const label =
      { all_in: 'All in', fixed_pct: 'Fixed % of equity', risk_based: 'Risk-based' }[sizing] ??
      sizing
    params.push({
      label: 'Position sizing',
      value: label,
      isDefault: sizing === ENGINE_DEFAULTS.position_sizing,
    })
  }
  if (typeof s.allow_short === 'boolean')
    params.push({
      label: 'Short selling',
      value: s.allow_short ? 'On' : 'Off',
      isDefault: !s.allow_short,
    })
  const lev = num(s.max_leverage)
  if (lev !== null)
    params.push({
      label: 'Max leverage',
      value: `${lev}×`,
      isDefault: lev === ENGINE_DEFAULTS.max_leverage,
    })
  const capital = num(s.initial_capital)
  if (capital !== null)
    params.push({
      label: 'Starting capital',
      value: `$${capital.toLocaleString('en-US')}`,
      isDefault: true,
    })

  return {
    entry: list(s.buy_rules).map(describeRule),
    exit: list(s.sell_rules).map(describeRule),
    params,
  }
}
