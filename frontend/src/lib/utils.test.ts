import { describe, expect, it } from 'vitest'
import {
  clamp,
  fmtCurrency,
  fmtCurrencyCompact,
  fmtDate,
  fmtDatePlain,
  fmtMarketCap,
  fmtNumber,
  fmtPct,
  fmtSigned,
  timeAgo,
} from './utils'

describe('fmtCurrency', () => {
  it('formats USD with two decimals', () => {
    expect(fmtCurrency(1234.567)).toBe('$1,234.57')
  })
  it('returns em dash for null/undefined/NaN', () => {
    expect(fmtCurrency(null)).toBe('—')
    expect(fmtCurrency(undefined)).toBe('—')
    expect(fmtCurrency(NaN)).toBe('—')
  })
  it('honors digits param', () => {
    expect(fmtCurrency(12.5, 0)).toBe('$13')
  })
})

describe('fmtCurrencyCompact', () => {
  it('abbreviates billions/millions/thousands', () => {
    expect(fmtCurrencyCompact(1.2e9)).toBe('$1.20B')
    expect(fmtCurrencyCompact(1.84e6)).toBe('$1.84M')
    expect(fmtCurrencyCompact(1234)).toBe('$1.2K')
    expect(fmtCurrencyCompact(99)).toBe('$99.00')
  })
  it('handles negatives', () => {
    expect(fmtCurrencyCompact(-2.5e9)).toBe('$-2.50B')
  })
})

describe('fmtMarketCap', () => {
  it('handles trillions', () => {
    expect(fmtMarketCap(2.84e12)).toBe('$2.84T')
  })
  it('handles billions', () => {
    expect(fmtMarketCap(184.5e9)).toBe('$184.50B')
  })
  it('falls back to currency for small values', () => {
    expect(fmtMarketCap(12345)).toBe('$12,345')
  })
})

describe('fmtPct', () => {
  it('adds a plus sign for positive when signed', () => {
    expect(fmtPct(12.4)).toBe('+12.4%')
    expect(fmtPct(-3.1)).toBe('-3.1%')
    expect(fmtPct(0)).toBe('0.0%')
  })
  it('supports unsigned and digits', () => {
    expect(fmtPct(12.345, 2, false)).toBe('12.35%')
  })
})

describe('fmtSigned', () => {
  it('signs positive values', () => {
    expect(fmtSigned(3.14159)).toBe('+3.14')
    expect(fmtSigned(-2)).toBe('-2.00')
  })
})

describe('timeAgo', () => {
  it('returns just now for fresh timestamps', () => {
    expect(timeAgo(new Date().toISOString())).toBe('just now')
  })
  it('returns minutes/hours/days', () => {
    const now = Date.now()
    expect(timeAgo(new Date(now - 5 * 60_000).toISOString())).toBe('5m ago')
    expect(timeAgo(new Date(now - 3 * 3_600_000).toISOString())).toBe('3h ago')
    expect(timeAgo(new Date(now - 2 * 86_400_000).toISOString())).toBe('2d ago')
  })
  it('returns empty for invalid input', () => {
    expect(timeAgo(null)).toBe('')
    expect(timeAgo('garbage')).toBe('')
  })
})

describe('fmtDatePlain', () => {
  it('formats YYYY-MM-DD without timezone shift', () => {
    expect(fmtDatePlain('2026-03-04')).toBe('Mar 4, 2026')
  })
  it('passes through malformed input', () => {
    expect(fmtDatePlain('nope')).toBe('nope')
  })
})

describe('fmtDate', () => {
  it('formats ISO dates', () => {
    expect(fmtDate('2026-03-04T10:00:00Z')).toBe('Mar 4, 2026')
  })
  it('em dash on invalid', () => {
    expect(fmtDate('junk')).toBe('—')
  })
})

describe('fmtNumber', () => {
  it('adds thousands separators', () => {
    expect(fmtNumber(1234567)).toBe('1,234,567')
  })
})

describe('clamp', () => {
  it('clamps to the range', () => {
    expect(clamp(5, 0, 10)).toBe(5)
    expect(clamp(-1, 0, 10)).toBe(0)
    expect(clamp(11, 0, 10)).toBe(10)
  })
})
