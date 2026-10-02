/**
 * Central SEO metadata registry.
 *
 * `Seo` (src/components/Seo.tsx) reads this on every route change to set
 * document.title, description, OG/Twitter tags, canonical, and robots meta.
 * The site origin comes from VITE_SITE_URL (see .env.example) with a sane
 * fallback so local dev still renders valid tags.
 */

export const SITE = {
  name: 'Stratoscan',
  tagline: 'Bridge from idea to candidate list',
  /** Canonical origin — override via VITE_SITE_URL at build/deploy time. */
  url: (import.meta.env.VITE_SITE_URL as string | undefined) ?? 'https://stratoscan.com',
  description:
    'Turn plain-English investing ideas into ranked, backtested stock lists. Stratoscan screens the market for you — no spreadsheet required.',
  /** Absolute path (public/) to the share image. */
  image: '/og-image.png',
  twitterHandle: '@stratoscan',
} as const

export interface RouteMeta {
  path: string
  title: string
  description: string
  /** Authenticated app routes should not be indexed. */
  noindex: boolean
}

const PUBLIC: RouteMeta[] = [
  {
    path: '/',
    title: 'Stratoscan — Bridge from idea to candidate list',
    description: SITE.description,
    noindex: false,
  },
  {
    path: '/features',
    title: 'Features — Stratoscan',
    description:
      'Plain-English screening, real-time fundamentals, momentum indicators, and one-click backtesting — everything you need to go from thesis to trade.',
    noindex: false,
  },
  {
    path: '/how-it-works',
    title: 'How It Works — Stratoscan',
    description:
      'Type your investing thesis in plain English. Stratoscan translates it into criteria, surfaces ranked candidates, and backtests the strategy.',
    noindex: false,
  },
  {
    path: '/pricing',
    title: 'Pricing — Stratoscan',
    description:
      'Simple, transparent pricing for a screener that actually understands your thesis. Start free — no spreadsheet required.',
    noindex: false,
  },
  {
    path: '/about',
    title: 'About — Stratoscan',
    description:
      'We built Stratoscan because traditional screeners bury you in filters. Our mission: turn investing ideas into candidate lists in seconds.',
    noindex: false,
  },
  {
    path: '/contact',
    title: 'Contact — Stratoscan',
    description:
      'Questions, feedback, or partnership ideas? Get in touch with the Stratoscan team.',
    noindex: false,
  },
  {
    path: '/terms',
    title: 'Terms of Service — Stratoscan',
    description:
      'The terms that govern your use of Stratoscan — acceptance, obligations, disclaimers, and more.',
    noindex: false,
  },
  {
    path: '/privacy',
    title: 'Privacy Policy — Stratoscan',
    description:
      'How Stratoscan collects, uses, and protects your data. We collect as little as possible — and never sell it.',
    noindex: false,
  },
  {
    path: '/risk',
    title: 'Risk Disclaimer — Stratoscan',
    description:
      'What a backtest can and cannot tell you: survivorship bias, look-ahead limits, data that can be stale, and why a simulated result is not investment advice.',
    noindex: false,
  },
  {
    path: '/login',
    title: 'Log In — Stratoscan',
    description:
      'Log in to Stratoscan to run screens, backtest strategies, and track your watchlist.',
    noindex: false,
  },
  {
    path: '/signup',
    title: 'Sign Up — Stratoscan',
    description:
      'Create your free Stratoscan account and start turning plain-English ideas into ranked, backtested stock lists.',
    noindex: false,
  },
  {
    path: '/verify-email',
    title: 'Verify Email — Stratoscan',
    description: 'Confirm your email address to activate your Stratoscan account.',
    noindex: false,
  },
]

/** Authenticated app routes — real titles, but noindex. */
const APP: RouteMeta[] = [
  {
    path: '/app',
    title: 'Dashboard — Stratoscan',
    description:
      'Your Stratoscan command center: saved screeners, recent backtests, and market overview.',
    noindex: true,
  },
  {
    path: '/app/screener',
    title: 'Screener — Stratoscan',
    description: 'Run a plain-English stock screen and explore ranked candidates.',
    noindex: true,
  },
  {
    path: '/app/backtest',
    title: 'Backtest — Stratoscan',
    description: 'Validate a strategy against historical data with equity curves and key metrics.',
    noindex: true,
  },
  {
    path: '/app/news',
    title: 'News — Stratoscan',
    description: 'Follow market news with AI sentiment scores.',
    noindex: true,
  },
  {
    path: '/app/settings',
    title: 'Settings — Stratoscan',
    description: 'Manage your Stratoscan account settings.',
    noindex: true,
  },
]

const NOT_FOUND: RouteMeta = {
  path: '/404',
  title: 'Page Not Found — Stratoscan',
  description: 'The page you are looking for does not exist.',
  noindex: true,
}

function baseMeta(): RouteMeta {
  return {
    path: '/',
    title: SITE.name,
    description: SITE.description,
    noindex: false,
  }
}

/** Pick the metadata block for a pathname (public pages win on prefix). */
export function routeMeta(pathname: string): RouteMeta {
  const clean = pathname.replace(/\/+$/, '') || '/'
  // Exact public-page match first.
  for (const m of PUBLIC) {
    if (m.path === clean) return m
  }
  // Authenticated app surface — exact /app and everything under /app/.
  if (clean === '/app' || clean.startsWith('/app/')) {
    // Stock detail gets its own title (still noindex).
    if (clean.startsWith('/app/stocks/')) {
      return {
        path: '/app',
        title: 'Stock Detail — Stratoscan',
        description: APP[0].description,
        noindex: true,
      }
    }
    for (const m of APP) {
      if (m.path !== '/app' && clean.startsWith(m.path)) return m
    }
    return APP[0]
  }
  if (clean === '/404') return NOT_FOUND
  return baseMeta()
}
