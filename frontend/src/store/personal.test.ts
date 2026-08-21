import { beforeEach, describe, expect, it } from 'vitest'
import { usePersonal } from './personal'
import type { BacktestResponse } from '@/lib/api/types'

function reset() {
  usePersonal.setState({
    watchlist: [],
    savedScreeners: [],
    recentBacktests: [],
  })
}

describe('personal store', () => {
  beforeEach(() => {
    reset()
  })

  it('toggles symbols on/off the watchlist', () => {
    usePersonal.getState().toggleWatch('NVDA')
    expect(usePersonal.getState().watchlist).toEqual(['NVDA'])
    expect(usePersonal.getState().isWatched('NVDA')).toBe(true)

    usePersonal.getState().toggleWatch('NVDA')
    expect(usePersonal.getState().watchlist).toEqual([])
    expect(usePersonal.getState().isWatched('NVDA')).toBe(false)
  })

  it('saves screeners newest-first and dedupes by query', () => {
    usePersonal.getState().saveScreener('momentum breakout stocks')
    usePersonal.getState().saveScreener('high-dividend energy companies')
    usePersonal.getState().saveScreener('momentum breakout stocks')

    const screeners = usePersonal.getState().savedScreeners
    expect(screeners).toHaveLength(2)
    expect(screeners[0].query).toBe('momentum breakout stocks')
  })

  it('removes a saved screener by id', () => {
    usePersonal.getState().saveScreener('value stocks')
    const id = usePersonal.getState().savedScreeners[0].id
    usePersonal.getState().removeScreener(id)
    expect(usePersonal.getState().savedScreeners).toHaveLength(0)
  })

  it('adds recent backtests newest-first, deduped by query', () => {
    const res = { query: 'oversold tech' } as unknown as BacktestResponse
    usePersonal.getState().addRecentBacktest(res)
    usePersonal
      .getState()
      .addRecentBacktest({ query: 'dividend energy' } as unknown as BacktestResponse)
    usePersonal.getState().addRecentBacktest(res)

    const backtests = usePersonal.getState().recentBacktests
    expect(backtests).toHaveLength(2)
    expect(backtests[0].query).toBe('oversold tech')
    expect(backtests[0].savedAt).toBeTruthy()
  })

  it('clears backtests', () => {
    usePersonal.getState().addRecentBacktest({ query: 'x' } as unknown as BacktestResponse)
    usePersonal.getState().clearBacktests()
    expect(usePersonal.getState().recentBacktests).toHaveLength(0)
  })
})
