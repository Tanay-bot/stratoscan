import { describe, expect, it } from 'vitest'
import { makeSeries, seeded, toPath } from './series'

describe('seeded', () => {
  it('is deterministic for the same seed', () => {
    const a = seeded(42)
    const b = seeded(42)
    const seqA = [a(), a(), a()]
    const seqB = [b(), b(), b()]
    expect(seqA).toEqual(seqB)
  })

  it('differs for different seeds', () => {
    expect(seeded(1)()).not.toBe(seeded(2)())
  })

  it('produces values in [0, 1)', () => {
    const rng = seeded(7)
    for (let i = 0; i < 100; i++) {
      const v = rng()
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThan(1)
    }
  })
})

describe('makeSeries', () => {
  it('returns n points', () => {
    const pts = makeSeries(seeded(1), 50, 0.001, 0.05)
    expect(pts).toHaveLength(50)
  })

  it('normalizes y into the band', () => {
    const pts = makeSeries(seeded(1), 200, 0.001, 0.05, 8, 68)
    for (const p of pts) {
      expect(p.y).toBeGreaterThanOrEqual(8)
      expect(p.y).toBeLessThanOrEqual(68)
    }
  })

  it('spans x from 0 to 100', () => {
    const pts = makeSeries(seeded(3), 100, 0, 0.05)
    expect(pts[0].x).toBe(0)
    expect(pts[99].x).toBe(100)
  })

  it('degenerates safely when all values are equal', () => {
    const flat = () => 0.5
    const pts = makeSeries(flat, 10, 0, 0)
    for (const p of pts) {
      expect(Number.isFinite(p.y)).toBe(true)
    }
  })
})

describe('toPath', () => {
  it('builds an SVG path string', () => {
    const pts = [
      { x: 0, y: 50 },
      { x: 50, y: 30 },
      { x: 100, y: 10 },
    ]
    expect(toPath(pts)).toBe('M0.0,50.0 L50.0,30.0 L100.0,10.0')
  })
})
