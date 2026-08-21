import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { routeMeta, SITE } from '@/lib/seo'

/**
 * Route-aware SEO manager — the dependency-free stand-in for React Helmet.
 *
 * Rendered once (inside <BrowserRouter>) it updates document.title and the
 * description / OG / Twitter / canonical / robots tags on every route change.
 * Tags are upserted (created once, then mutated) so server-rendered or
 * statically-served defaults in index.html stay consistent with the SPA state.
 */

function upsertMeta(attr: 'name' | 'property', key: string, content: string): void {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.setAttribute('content', content)
}

function upsertLink(rel: string, href: string): void {
  let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)
  if (!el) {
    el = document.createElement('link')
    el.setAttribute('rel', rel)
    document.head.appendChild(el)
  }
  el.setAttribute('href', href)
}

function absolute(path: string): string {
  if (/^https?:\/\//.test(path)) return path
  return `${SITE.url}${path}`
}

export function Seo(): null {
  const { pathname } = useLocation()

  useEffect(() => {
    const meta = routeMeta(pathname)
    const url = absolute(meta.path)

    document.title = meta.title

    upsertMeta('name', 'description', meta.description)

    // Open Graph
    upsertMeta('property', 'og:title', meta.title)
    upsertMeta('property', 'og:description', meta.description)
    upsertMeta('property', 'og:type', 'website')
    upsertMeta('property', 'og:url', url)
    upsertMeta('property', 'og:image', absolute(SITE.image))
    upsertMeta('property', 'og:site_name', SITE.name)

    // Twitter Card
    upsertMeta('name', 'twitter:card', 'summary_large_image')
    upsertMeta('name', 'twitter:title', meta.title)
    upsertMeta('name', 'twitter:description', meta.description)
    upsertMeta('name', 'twitter:image', absolute(SITE.image))
    if (SITE.twitterHandle) upsertMeta('name', 'twitter:site', SITE.twitterHandle)

    // Canonical + robots
    upsertLink('canonical', url)
    upsertMeta('name', 'robots', meta.noindex ? 'noindex, nofollow' : 'index, follow')
  }, [pathname])

  return null
}
