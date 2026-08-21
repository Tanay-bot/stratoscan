/* global console */
/**
 * Regression test for the walk-forward unit-conversion bug (systematic-debugging).
 *
 * The backend `walk_forward_analysis` (src/backtester.py) returns TOTAL RETURN
 * as a decimal MULTIPLIER (0.637 = +63.7%, 2.6 = +260%). The frontend must
 * multiply by 100 before fmtPct.
 *
 * ROOT CAUSE (fixed): Robustness.tsx had `fmtPct(x ?? 0 * 100, 1)` — JS parses
 * that as `x ?? (0 * 100)` because `*` binds tighter than `??`, so the `* 100`
 * silently NEVER applied when x was present. In-sample and window min/max were
 * therefore shown 100x too small, which made the (correctly scaled) median
 * appear OUTSIDE its own [min, max] range in the live UI.
 *
 * This guard asserts the FIXED arithmetic: returns ×100, median inside range.
 * If anyone reverts the fix (removes the parentheses), this fails.
 *
 * Run: node scripts/verify_walkforward_scale.mjs
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// Mirror of frontend/src/lib/utils.ts `fmtPct(value, digits = 1, signed = true)`.
const fmtPct = (value, digits = 1, signed = true) => {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const sign = signed && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}%`
}

// Backend values from a real live run (decimal multipliers).
const is = { total_return: 2.6 }                            // +260%
const oos = { total_return: 0.637 }                         // +63.7%
const rolling = { min_return: 0.3, max_return: 1.2, median_return: 0.567 } // +30% / +120% / +56.7%

// ── The FIXED expressions (must stay parenthesized: (x ?? 0) * 100) ────────
const isPct = fmtPct((is?.total_return ?? 0) * 100, 1)
const oosPct = fmtPct((oos?.total_return ?? 0) * 100, 1)
const minPct = fmtPct((rolling?.min_return ?? 0) * 100, 1)
const maxPct = fmtPct((rolling?.max_return ?? 0) * 100, 1)
const medPct = fmtPct((rolling?.median_return ?? 0) * 100, 1)

console.log('in-sample:', isPct, '| OOS:', oosPct, '| window:', `${minPct} – ${maxPct}`, '| median:', medPct)

assert.equal(isPct, '+260.0%', 'in-sample must show +260.0%, not +2.6%')
assert.equal(minPct, '+30.0%', 'window min must show +30.0%, not +0.3%')
assert.equal(maxPct, '+120.0%', 'window max must show +120.0%, not +1.2%')
assert.equal(oosPct, '+63.7%', 'out-of-sample unchanged')

// OOS/IS ratio is dimensionless: (0.637 / 2.6) * 100 = 24.5 -> 25%
assert.equal(`${((oos.total_return / is.total_return) * 100).toFixed(0)}%`, '25%')

// Invariant: median must sit inside [min, max] — broken by the old bug.
const medNum = parseFloat(medPct)
const minNum = parseFloat(minPct)
const maxNum = parseFloat(maxPct)
assert.ok(medNum > minNum && medNum < maxNum, 'median must sit inside the corrected min/max range')

// Guard the source itself: the unparenthesized pattern must not reappear.
const src = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), '../src/components/backtest/Robustness.tsx'),
  'utf8',
)
assert.ok(!src.includes('?? 0 * 100'), 'Robustness.tsx must not contain the unparenthesized `?? 0 * 100` pattern')

console.log('\n✅ Walk-forward display scale verified: all returns ×100, median inside [min,max], source guarded.')
