import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Check, Gem, Heart, Infinity as InfinityIcon, Sparkles } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const INCLUDED = [
  'Unlimited natural-language screens',
  '10 years of backtest history per run',
  'Monte Carlo + confidence intervals',
  'Walk-forward validation & SPY benchmark',
  'News feed with LLM sentiment scoring',
  'Watchlist, saved screens & backtest history',
]

const FREE_FEATURES: { label: string; value: string }[] = [
  { label: 'Screening queries', value: 'Unlimited' },
  { label: 'Backtest runs', value: '10 / day' },
  { label: 'History depth', value: '10 years' },
  { label: 'News sentiment', value: 'Included' },
  { label: 'API access', value: 'Full REST' },
  { label: 'Price', value: '$0 forever' },
]

const FAQS = [
  {
    q: 'Is it really free?',
    a: 'Yes. Stratoscan has no paid tier and no plans for one. No credits, no trials, no “upgrade to unlock” — free is the plan, not a promotion.',
  },
  {
    q: 'Why 10 backtests per day?',
    a: 'Each backtest replays years of history across thousands of simulated trades. The daily cap keeps results fast for everyone — not because we charge for more.',
  },
  {
    q: 'What is in the full version?',
    a: 'There is no full version. Every feature ships to every account: Monte Carlo, walk-forward validation, sentiment, exports — all included.',
  },
  {
    q: 'How do you make money?',
    a: 'Being free is the strategy. We built Stratoscan to stay fast and generous for everyone — and if that ever changes, everything listed on this page stays free.',
  },
]

export function Pricing() {
  const navigate = useNavigate()

  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      <section className="relative overflow-hidden">
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[560px] -translate-x-1/2 rounded-full bg-neon/[0.08] blur-[100px]"
          aria-hidden="true"
        />
        <div
          className="pointer-events-none absolute bottom-0 right-[6%] h-64 w-64 rounded-full bg-grape/[0.12] blur-[90px]"
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-3xl px-5 pb-16 pt-20 text-center">
          <motion.p
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="inline-flex items-center gap-2 rounded-full border border-gain/25 bg-gain/10 px-3.5 py-1.5 text-2xs font-semibold uppercase tracking-wider text-gain-soft"
          >
            <Sparkles className="h-3 w-3" aria-hidden="true" /> No paywall. Ever.
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="mt-6 text-4xl font-bold sm:text-5xl"
          >
            One plan. <span className="text-gradient">Zero dollars.</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-text-mid"
          >
            Every feature, for every account, forever. No tiers, no credits, no “upgrade to unlock”.
          </motion.p>
        </div>
      </section>

      {/* Plan card */}
      <section className="mx-auto max-w-4xl px-5 pb-20">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="glass-strong relative overflow-hidden rounded-3xl shadow-card"
        >
          <div
            className="pointer-events-none absolute -top-20 right-0 h-48 w-72 rounded-full bg-grape/15 blur-[80px]"
            aria-hidden="true"
          />

          <div className="relative grid gap-0 md:grid-cols-[1.2fr_1fr]">
            {/* Left: plan */}
            <div className="p-8 sm:p-10">
              <div className="flex items-center gap-3">
                <h2 className="text-2xl font-bold">Free</h2>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-neon/25 bg-neon/10 px-3 py-1 text-2xs font-semibold text-neon">
                  <Heart className="h-3 w-3" aria-hidden="true" /> The only plan
                </span>
              </div>
              <div className="mt-5 flex items-end gap-2">
                <span className="font-mono text-6xl font-bold tabular text-text-hi">$0</span>
                <span className="pb-2 text-sm text-text-low">/ forever</span>
              </div>
              <p className="mt-4 max-w-sm text-sm leading-relaxed text-text-mid">
                Everything the terminal does — screening, backtesting, sentiment, exports. The full
                product, with the full history depth.
              </p>

              <div className="mt-6 space-y-3">
                {INCLUDED.map((f) => (
                  <div key={f} className="flex items-start gap-3 text-sm text-text-hi">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gain/15">
                      <Check className="h-3 w-3 text-gain-soft" aria-hidden="true" />
                    </span>
                    {f}
                  </div>
                ))}
              </div>

              <Button size="lg" className="mt-8 w-full" onClick={() => navigate('/signup')}>
                Start for free <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>

            {/* Right: spec table */}
            <div className="border-t border-white/[0.08] bg-ink-deep/40 p-8 sm:p-10 md:border-l md:border-t-0">
              <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
                What you get
              </p>
              <dl className="mt-5 space-y-4">
                {FREE_FEATURES.map((f) => (
                  <div key={f.label} className="flex items-center justify-between gap-4">
                    <dt className="text-sm text-text-mid">{f.label}</dt>
                    <dd className="flex items-center gap-1.5 font-mono text-sm font-semibold tabular text-text-hi">
                      {f.value === '$0 forever' ? (
                        <>
                          <Gem className="h-3.5 w-3.5 text-neon" aria-hidden="true" /> $0 forever
                        </>
                      ) : f.value === 'Unlimited' ? (
                        <>
                          <InfinityIcon className="h-3.5 w-3.5 text-gain-soft" aria-hidden="true" />{' '}
                          Unlimited
                        </>
                      ) : (
                        f.value
                      )}
                    </dd>
                  </div>
                ))}
              </dl>

              <div className="mt-8 rounded-xl border border-gain/20 bg-gain/[0.06] p-4">
                <p className="flex items-center gap-2 text-xs font-semibold text-gain-soft">
                  <Heart className="h-3.5 w-3.5" aria-hidden="true" /> Honest pricing note
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-text-mid">
                  If a paid tier ever exists, everything listed here stays free. We’d rather earn
                  your trust than your credit card.
                </p>
              </div>
            </div>
          </div>
        </motion.div>
      </section>

      {/* FAQ */}
      <section className="mx-auto max-w-3xl px-5 pb-24">
        <div className="mb-10 text-center">
          <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">Questions</p>
          <h2 className="mt-3 text-3xl font-bold">Fair questions, straight answers</h2>
        </div>
        <div className="space-y-3">
          {FAQS.map((f, i) => (
            <motion.details
              key={f.q}
              initial={{ opacity: 0, y: 14 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.4, delay: i * 0.06 }}
              className="glass-hover group rounded-2xl px-6 py-5"
              open={i === 0}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-sm font-semibold text-text-hi">
                {f.q}
                <span
                  className={cn(
                    'flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-text-mid transition-transform duration-300 group-open:rotate-45',
                  )}
                  aria-hidden="true"
                >
                  +
                </span>
              </summary>
              <p className="mt-3 text-sm leading-relaxed text-text-mid">{f.a}</p>
            </motion.details>
          ))}
        </div>
      </section>

      <Footer />
    </div>
  )
}
