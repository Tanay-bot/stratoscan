import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const { explainResult } = vi.hoisted(() => ({ explainResult: vi.fn() }))
vi.mock('@/lib/api/services', () => ({ explainResult }))

import { ExplainPanel } from './ExplainPanel'
import type { Explanation } from '@/lib/api/types'

const reading = (over: Partial<Explanation> = {}): Explanation => ({
  result_hash: 'h',
  basis: 'single',
  findings: [
    {
      code: 'lost_money',
      tone: 'caution',
      text: 'The strategy lost money: -12.0% over the period.',
    },
    { code: 'beat_benchmark', tone: 'positive', text: 'It finished 3.0 points ahead of SPY.' },
  ],
  narrative: {
    summary: 'The strategy lost 12.0% over the period.',
    points: ['Losers were larger.'],
  },
  narrative_status: 'ok',
  disclaimer: 'It is not investment advice and says nothing about the future.',
  cached: false,
  facts: {},
  ...over,
})

beforeEach(() => explainResult.mockReset())

describe('ExplainPanel', () => {
  it('shows the written summary, the findings and the disclaimer', async () => {
    explainResult.mockResolvedValue(reading())
    render(<ExplainPanel jobId="job-1" />)
    expect(await screen.findByText('The strategy lost 12.0% over the period.')).toBeInTheDocument()
    expect(screen.getByText('Losers were larger.')).toBeInTheDocument()
    expect(screen.getByText(/lost money: -12\.0%/)).toBeInTheDocument()
    expect(screen.getByText(/not investment advice/)).toBeInTheDocument()
    expect(explainResult).toHaveBeenCalledWith('job-1')
  })

  it('without a narrative (providers down or withheld) still shows the findings', async () => {
    explainResult.mockResolvedValue(reading({ narrative: null, narrative_status: 'unavailable' }))
    render(<ExplainPanel jobId="job-1" />)
    expect(await screen.findByText(/lost money/)).toBeInTheDocument()
    expect(screen.getByText(/written summary isn.t available/)).toBeInTheDocument()
  })

  it('a failed request says the numbers stand, and can retry', async () => {
    explainResult.mockRejectedValueOnce(new Error('429')).mockResolvedValueOnce(reading())
    render(<ExplainPanel jobId="job-1" />)
    expect(await screen.findByText(/numbers above are unaffected/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(await screen.findByText(/lost money/)).toBeInTheDocument()
  })
})
