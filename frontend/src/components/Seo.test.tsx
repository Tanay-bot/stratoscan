import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { Seo } from './Seo'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Seo />
      <Routes>
        <Route path="/" element={<div>home</div>} />
        <Route path="/features" element={<div>features</div>} />
        <Route path="/pricing" element={<div>pricing</div>} />
        <Route path="/signup" element={<div>signup</div>} />
        <Route path="/app/screener" element={<div>screener</div>} />
        <Route path="/app/stocks/:symbol" element={<div>stock</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

const meta = (attr: 'name' | 'property', key: string) =>
  document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)?.getAttribute('content') ??
  ''

// Isolate tests: clear the tags Seo upserts so state doesn't leak across
// tests in the shared jsdom document.head.
function cleanupHead() {
  document.head
    .querySelectorAll(
      'meta[name="description"], meta[property^="og:"], meta[name^="twitter:"], meta[name="robots"], link[rel="canonical"]',
    )
    .forEach((el) => el.remove())
  document.title = ''
}

beforeEach(() => cleanupHead())
afterEach(() => cleanupHead())

describe('Seo', () => {
  it('sets title, description, canonical and OG tags on the landing page', () => {
    renderAt('/')
    expect(document.title).toBe('Stratoscan — Bridge from idea to candidate list')
    expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toMatch(
      /stratoscan\.com\/$/,
    )
    expect(meta('property', 'og:title')).toContain('Bridge from idea')
    expect(meta('property', 'og:type')).toBe('website')
    expect(meta('name', 'twitter:card')).toBe('summary_large_image')
    expect(meta('name', 'robots')).toBe('index, follow')
    expect(meta('property', 'og:image')).toMatch(/og-image\.png$/)
  })

  it('updates tags per route and keeps canonical synced', () => {
    const { unmount } = renderAt('/pricing')
    expect(document.title).toBe('Pricing — Stratoscan')
    expect(meta('property', 'og:title')).toBe('Pricing — Stratoscan')
    expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toMatch(
      /\/pricing$/,
    )
    unmount()
  })

  it('marks protected app routes noindex', () => {
    const { unmount } = renderAt('/app/screener')
    expect(meta('name', 'robots')).toBe('noindex, nofollow')
    expect(document.title).toBe('Screener — Stratoscan')
    unmount()
  })

  it('keeps description synced across navigation', () => {
    const { unmount } = renderAt('/features')
    expect(meta('name', 'description')).toContain('Plain-English screening')
    unmount()
  })
})
