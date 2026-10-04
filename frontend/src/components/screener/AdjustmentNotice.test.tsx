import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AdjustmentNotice } from './AdjustmentNotice'

describe('AdjustmentNotice', () => {
  it('renders nothing when the screen ran exactly as asked', () => {
    const { container } = render(<AdjustmentNotice dataQuality={{ stale_count: 0 }} />)
    expect(container).toBeEmptyDOMElement()
    expect(render(<AdjustmentNotice />).container).toBeEmptyDOMElement()
  })

  it('names every filter the zero-match rescue dropped', () => {
    render(
      <AdjustmentNotice dataQuality={{ relaxed_from: ['avg_volume ≥ 1000000', 'beta ≤ 0.8'] }} />,
    )
    expect(screen.getByRole('status')).toHaveTextContent('We loosened your screen.')
    expect(screen.getByText('avg_volume ≥ 1000000')).toBeInTheDocument()
    expect(screen.getByText('beta ≤ 0.8')).toBeInTheDocument()
  })

  it('explains a condition dropped as contradictory', () => {
    render(
      <AdjustmentNotice
        dataQuality={{
          contradiction_resolved: [
            "dropped 'price > SMA50' — contradicts oversold RSI ≤ 30 (oversold stocks sit below their average)",
          ],
        }}
      />,
    )
    expect(screen.getByRole('status')).toHaveTextContent("dropped 'price > SMA50'")
  })

  it('ignores malformed values instead of crashing', () => {
    const { container } = render(
      <AdjustmentNotice dataQuality={{ relaxed_from: 'x', contradiction_resolved: [1, null] }} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
