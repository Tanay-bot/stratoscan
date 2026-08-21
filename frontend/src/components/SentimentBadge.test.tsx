import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SentimentBadge } from './SentimentBadge'

// The badge intentionally renders its label TWICE when not compact: the
// visible text and an .sr-only copy for screen readers. Use getAllByText
// (or the sr-only selector) instead of getByText to match both.
describe('SentimentBadge', () => {
  it('labels bullish for scores >= 0.15', () => {
    render(<SentimentBadge score={0.8} />)
    expect(screen.getAllByText('Bullish').length).toBeGreaterThan(0)
  })

  it('labels bearish for scores <= -0.15', () => {
    render(<SentimentBadge score={-0.6} />)
    expect(screen.getAllByText('Bearish').length).toBeGreaterThan(0)
  })

  it('labels neutral for scores in the middle band', () => {
    render(<SentimentBadge score={0.05} />)
    expect(screen.getAllByText('Neutral').length).toBeGreaterThan(0)
  })

  it('labels neutral when no score is present', () => {
    render(<SentimentBadge score={null} />)
    expect(screen.getAllByText('Neutral').length).toBeGreaterThan(0)
  })

  it('prefers an explicit label', () => {
    render(<SentimentBadge score={0.9} label="Very bullish" />)
    expect(screen.getAllByText('Very bullish').length).toBeGreaterThan(0)
  })

  it('keeps the tone label for screen readers in compact mode', () => {
    const { container } = render(<SentimentBadge score={-0.9} compact />)
    expect(container.querySelector('.sr-only')?.textContent).toBe('Bearish')
  })

  it('exposes the tone to screen readers even when the label is shown visually', () => {
    const { container } = render(<SentimentBadge score={0.2} />)
    expect(container.querySelector('.sr-only')?.textContent).toBe('Bullish')
  })
})
