import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RulesPanel } from './RulesPanel'

const strategy = {
  buy_rules: ['rsi < 35.0'],
  sell_rules: ['rsi > 70'],
  fee_rate: 0.001,
  slippage: 0.0005,
}

describe('RulesPanel (layer 3)', () => {
  it('shows the rules in English, not JSON', () => {
    render(<RulesPanel strategy={strategy} onRerun={vi.fn()} />)
    expect(screen.getByText('RSI(14) below 35')).toBeInTheDocument()
    expect(screen.getByText('RSI(14) above 70')).toBeInTheDocument()
    expect(screen.queryByText(/buy_rules/)).toBeNull()
    expect(screen.getAllByText('default').length).toBeGreaterThan(0)
  })

  it('re-runs with an edited rule, without retyping the query', async () => {
    const onRerun = vi.fn()
    render(<RulesPanel strategy={strategy} onRerun={onRerun} />)
    const rerun = screen.getByRole('button', { name: /Re-run with these rules/ })
    expect(rerun).toBeDisabled() // nothing changed yet
    const buy = screen.getByLabelText('Buy when RSI below')
    await userEvent.clear(buy)
    await userEvent.type(buy, '30')
    await userEvent.click(rerun)
    expect(onRerun).toHaveBeenCalledWith({ rsiBuy: 30, rsiSell: 70 })
  })

  it('refuses crossed or out-of-range levels', async () => {
    render(<RulesPanel strategy={strategy} onRerun={vi.fn()} />)
    const sell = screen.getByLabelText('Sell when RSI above')
    await userEvent.clear(sell)
    await userEvent.type(sell, '20')
    expect(screen.getByRole('alert')).toHaveTextContent('buy level below the sell level')
    expect(screen.getByRole('button', { name: /Re-run with these rules/ })).toBeDisabled()
  })
})
