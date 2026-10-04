import { Link } from 'react-router-dom'
import { Logo } from '@/components/Logo'

const COLUMNS: { title: string; links: { label: string; to: string }[] }[] = [
  {
    title: 'Product',
    links: [
      { label: 'Screener', to: '/app/screener' },
      { label: 'Backtester', to: '/try' },
      { label: 'News & sentiment', to: '/app/news' },
      { label: 'Dashboard', to: '/app' },
    ],
  },
  {
    title: 'Company',
    links: [
      { label: 'How it works', to: '/how-it-works' },
      { label: 'Features', to: '/features' },
      { label: 'About', to: '/about' },
      { label: 'Pricing', to: '/pricing' },
      { label: 'Contact', to: '/contact' },
    ],
  },
  {
    title: 'Legal',
    links: [
      { label: 'Terms of Service', to: '/terms' },
      { label: 'Privacy Policy', to: '/privacy' },
      // The disclaimer is a first-class page, not a line of grey text: a
      // product that touches investment decisions has to say what a backtest
      // is and is not, somewhere a visitor can actually find it.
      { label: 'Risk Disclaimer', to: '/risk' },
    ],
  },
]

export function Footer() {
  return (
    <footer className="border-t border-white/[0.06]">
      <div className="mx-auto max-w-7xl px-5 py-14">
        <div className="grid gap-10 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <Logo />
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-text-mid">
              Bridge from idea to candidate list. A natural-language market terminal with 10 years
              of backtested history — free, forever.
            </p>
          </div>
          {COLUMNS.map((col) => (
            <div key={col.title}>
              <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
                {col.title}
              </p>
              <ul className="mt-4 space-y-2.5">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <Link
                      to={l.to}
                      className="text-sm text-text-mid transition-colors hover:text-text-hi"
                    >
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 flex flex-col items-start justify-between gap-4 border-t border-white/[0.06] pt-6 sm:flex-row sm:items-center">
          <p className="text-xs text-text-low">
            Stratoscan is a research tool, not a stock-picking service. No content on this site is
            financial advice.
          </p>
          <p className="flex items-center gap-2 text-xs text-text-low">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gain" aria-hidden="true" />
            Screener online · free forever · no paywall
          </p>
        </div>
      </div>
    </footer>
  )
}
