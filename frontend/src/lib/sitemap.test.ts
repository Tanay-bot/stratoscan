import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PUBLIC } from './seo'

const read = (name: string) => readFileSync(resolve(__dirname, '../../public', name), 'utf8')
const ORIGIN = 'https://stratoscan.com'

describe('sitemap.xml and robots.txt', () => {
  const sitemap = read('sitemap.xml')
  const locs = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1])

  it('lists exactly the indexable public pages from the SEO registry', () => {
    const paths = locs.map((l) => l.replace(ORIGIN, '') || '/')
    const indexable = PUBLIC.filter((m) => !m.noindex).map((m) => m.path)
    expect([...paths].sort()).toEqual([...indexable].sort())
  })

  it('uses absolute URLs on one origin, with no duplicates', () => {
    expect(locs.every((l) => l.startsWith(`${ORIGIN}/`) || l === `${ORIGIN}/`)).toBe(true)
    expect(new Set(locs).size).toBe(locs.length)
  })

  it('keeps crawlers out of the app and token pages, and points at the sitemap', () => {
    const robots = read('robots.txt')
    for (const path of ['/app/', '/verify-email', '/reset-password']) {
      expect(robots).toContain(`Disallow: ${path}`)
    }
    expect(robots).toContain(`Sitemap: ${ORIGIN}/sitemap.xml`)
    // Nothing the sitemap advertises may be disallowed.
    for (const l of locs) {
      const path = l.replace(ORIGIN, '') || '/'
      expect(robots).not.toMatch(new RegExp(`^Disallow: ${path}$`, 'm'))
    }
  })
})
