import { describe, expect, it } from 'vitest'
import { describeRule, describeStrategy, rsiThreshold } from './rules'

describe('describeRule', () => {
  it.each([
    ['rsi < 35', 'RSI(14) below 35'],
    ['rsi < 35.0', 'RSI(14) below 35'],
    ['rsi > 70', 'RSI(14) above 70'],
    ['price > sma_50', 'Price above the 50-day average'],
    ['price < sma_200', 'Price below the 200-day average'],
    ['sma_50 > sma_200', 'The 50-day average above the 200-day average'],
  ])('%s → %s', (rule, english) => expect(describeRule(rule)).toBe(english))

  it('passes through anything it does not understand rather than guessing', () => {
    expect(describeRule('weird rule')).toBe('weird rule')
    expect(describeRule('rsi ~ 3')).toBe('rsi ~ 3')
  })
})

describe('rsiThreshold', () => {
  it('finds the entry and exit RSI levels', () => {
    expect(rsiThreshold(['price > sma_50', 'rsi < 35.0'], '<')).toBe(35)
    expect(rsiThreshold(['rsi > 70'], '>')).toBe(70)
    expect(rsiThreshold(['price < sma_200'], '>')).toBeNull()
  })
})

describe('describeStrategy', () => {
  const echo = {
    buy_rules: ['price > sma_50', 'sma_50 > sma_200'],
    sell_rules: ['price < sma_200'],
    fee_rate: 0.001,
    slippage: 0.0005,
    stop_loss: null,
    take_profit: 0.2,
    position_sizing: 'all_in',
    allow_short: false,
    max_leverage: 1,
    initial_capital: 100000,
  }

  it('renders the rules the engine ran', () => {
    const v = describeStrategy(echo)
    expect(v.entry).toEqual([
      'Price above the 50-day average',
      'The 50-day average above the 200-day average',
    ])
    expect(v.exit).toEqual(['Price below the 200-day average'])
  })

  it('labels defaults as defaults, and a user choice as not', () => {
    const p = Object.fromEntries(describeStrategy(echo).params.map((x) => [x.label, x]))
    expect(p['Fee per trade']).toMatchObject({ value: '0.1%', isDefault: true })
    expect(p['Slippage per trade']).toMatchObject({ value: '0.05%', isDefault: true })
    expect(p['Stop loss']).toMatchObject({ value: 'Off', isDefault: true })
    expect(p['Take profit']).toMatchObject({ value: '20%', isDefault: false })
    expect(p['Starting capital'].value).toBe('$100,000')
  })

  it('survives a missing or malformed echo', () => {
    expect(describeStrategy(undefined).entry).toEqual([])
    expect(describeStrategy({ buy_rules: 'x', fee_rate: 'y' }).entry).toEqual([])
  })
})
