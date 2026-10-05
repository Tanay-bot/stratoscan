import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { StockRow } from './StockRow'
import { stockResultSchema } from '@/lib/api/schemas'

// Day 14: funds join the universe, and every one is labelled as a fund.
describe('StockRow asset label', () => {
  const base = {
    symbol: 'XLK',
    company_name: 'Technology Select Sector SPDR Fund',
    current_price: 250,
  }

  it('labels an ETF', () => {
    render(
      <MemoryRouter>
        <StockRow stock={{ ...base, asset_type: 'etf' }} index={0} />
      </MemoryRouter>,
    )
    expect(screen.getByText('ETF')).toBeInTheDocument()
  })

  it('does not label a stock (or a row from an older server without the field)', () => {
    render(
      <MemoryRouter>
        <StockRow stock={{ ...base, symbol: 'AAPL', asset_type: 'stock' }} index={0} />
        <StockRow stock={{ ...base, symbol: 'MSFT' }} index={1} />
      </MemoryRouter>,
    )
    expect(screen.queryByText('ETF')).toBeNull()
  })

  it('the contract accepts only stock | etf', () => {
    expect(stockResultSchema.safeParse({ ...base, asset_type: 'etf' }).success).toBe(true)
    expect(stockResultSchema.safeParse({ ...base, asset_type: 'crypto' }).success).toBe(false)
  })
})
