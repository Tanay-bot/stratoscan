import { describe, expect, it } from 'vitest'
import { routeMeta, SITE } from './seo'

describe('routeMeta', () => {
  it('maps the landing page', () => {
    const m = routeMeta('/')
    expect(m.title).toBe('Stratoscan — Has your investing idea ever worked?')
    expect(m.noindex).toBe(false)
    expect(m.path).toBe('/')
  })

  it('maps every public marketing page', () => {
    for (const path of ['/features', '/how-it-works', '/pricing', '/about', '/contact']) {
      const m = routeMeta(path)
      expect(m.title).toContain('Stratoscan')
      expect(m.noindex).toBe(false)
    }
  })

  it('maps login/signup as indexable', () => {
    expect(routeMeta('/login').noindex).toBe(false)
    expect(routeMeta('/signup').noindex).toBe(false)
    expect(routeMeta('/signup').title).toContain('Sign Up')
  })

  it('marks app routes noindex with dashboard default', () => {
    const m = routeMeta('/app')
    expect(m.noindex).toBe(true)
    expect(m.title).toBe('Dashboard — Stratoscan')
  })

  it('matches deeper app routes (screener/backtest/news/settings)', () => {
    expect(routeMeta('/app/screener').title).toBe('Screener — Stratoscan')
    expect(routeMeta('/app/screener').noindex).toBe(true)
    expect(routeMeta('/app/backtest').title).toBe('Backtest — Stratoscan')
    expect(routeMeta('/app/news').title).toBe('News — Stratoscan')
    expect(routeMeta('/app/settings').title).toBe('Settings — Stratoscan')
  })

  it('maps stock detail to a noindex title', () => {
    const m = routeMeta('/app/stocks/NVDA')
    expect(m.noindex).toBe(true)
    expect(m.title).toBe('Stock Detail — Stratoscan')
  })

  it('normalizes trailing slashes', () => {
    expect(routeMeta('/features/').path).toBe('/features')
  })

  it('returns a safe default for unknown routes', () => {
    const m = routeMeta('/some/unknown/path')
    expect(m.title).toBe(SITE.name)
    expect(m.noindex).toBe(false)
  })

  it('handles the 404 page', () => {
    expect(routeMeta('/404').title).toContain('Page Not Found')
    expect(routeMeta('/404').noindex).toBe(true)
  })

  it('exposes a canonical site origin', () => {
    expect(SITE.url).toMatch(/^https?:\/\//)
    expect(SITE.image).toMatch(/^\/og-image\.png$/)
  })
})
