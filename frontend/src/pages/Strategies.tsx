import { useNavigate } from 'react-router-dom'
import { ArrowRight, FlaskConical } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { Button } from '@/components/ui/button'
import { RiskDisclaimer } from '@/components/legal/RiskDisclaimer'
import { TEMPLATES, type StrategyTemplate } from '@/lib/templates'
import { track } from '@/lib/analytics'
import { useAuth } from '@/store/auth'

/**
 * `/strategies` — the public template library (Days 7–8).
 *
 * Every card is a real, runnable backtest: one click lands on the
 * backtester with the query filled in and running (no account needed for
 * the first three). Each card says exactly what the engine trades, so the
 * title never promises more than the simulation tests.
 */
export function Strategies() {
  const navigate = useNavigate()
  const signedIn = useAuth((s) => s.token !== null)

  const runTemplate = (t: StrategyTemplate) => {
    // The template id only — never its text (the analytics whitelist).
    track('template_used', { template: t.id })
    const q = encodeURIComponent(t.query)
    navigate(signedIn ? `/app/backtest?q=${q}` : `/try?q=${q}`)
  }

  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      <section className="relative mx-auto max-w-3xl px-5 pb-10 pt-20 text-center">
        <p className="inline-flex items-center gap-2 rounded-full border border-neon/25 bg-neon/10 px-3.5 py-1.5 text-2xs font-semibold uppercase tracking-wider text-neon">
          <FlaskConical className="h-3 w-3" aria-hidden="true" /> {TEMPLATES.length} ready-made
          strategies
        </p>
        <h1 className="mt-6 text-4xl font-bold sm:text-5xl">Strategy templates</h1>
        <p className="mx-auto mt-4 max-w-xl text-text-mid">
          Not sure what to type? Pick an idea and see how it did over the last 10 years against SPY
          — with fees, slippage and the robustness checks. Free, and no account needed to try.
        </p>
      </section>

      <div className="mx-auto max-w-6xl space-y-10 px-5 pb-16">
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {TEMPLATES.map((t) => (
            <li
              key={t.id}
              className="flex flex-col rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5"
            >
              <span className="mb-2 self-start rounded-full border border-white/10 px-2 py-0.5 text-2xs font-medium uppercase tracking-wider text-text-low">
                {t.category}
              </span>
              <h2 className="text-base font-semibold text-text-hi">{t.title}</h2>
              <p className="mt-1 text-sm text-text-mid">{t.idea}</p>
              <p className="mt-3 rounded-lg bg-black/30 px-3 py-2 font-mono text-xs leading-relaxed text-text-hi">
                “{t.query}”
              </p>
              <p className="mt-3 text-xs text-text-low">
                <span className="font-semibold text-text-mid">Simulated as:</span> {t.simulates}
              </p>
              <div className="mt-auto pt-4">
                <Button size="sm" onClick={() => runTemplate(t)} className="w-full">
                  Run this backtest <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <RiskDisclaimer />
      </div>

      <Footer />
    </div>
  )
}
