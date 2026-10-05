import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Check, Gem, Heart, Infinity as InfinityIcon, Sparkles } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { GRANDFATHER_LIMIT, NEVER_GATED, PLANS } from '@/lib/plans'
import { proPrice } from '@/lib/pricing'

const free = PLANS.free
const pro = PLANS.pro

const PLAN_ROWS: { label: string; free: string; pro: string }[] = [
  { label: 'Screening queries', free: 'Unlimited', pro: 'Unlimited' },
  {
    label: 'Backtests',
    free: `${free.backtestsPerMonth} / month`,
    pro: pro.backtestsPerMonth === null ? 'Unlimited' : `${pro.backtestsPerMonth} / month`,
  },
  {
    label: 'Saved strategies',
    free: String(free.savedStrategies),
    pro: String(pro.savedStrategies),
  },
  { label: 'CSV export', free: '—', pro: 'Included' },
  { label: 'Weekly strategy emails', free: '—', pro: 'Included' },
  { label: 'History depth', free: '10 years', pro: '10 years' },
]

const FAQS = [
  {
    q: 'Is the free plan really free?',
    a: `Yes, and it stays free: unlimited screens, ${free.backtestsPerMonth} backtests a month with the full 10 years of history, and every robustness check on every result. No card, no trial clock.`,
  },
  {
    q: 'What will never be part of a paid plan?',
    a: 'Anything that makes a number trustworthy: the full history, fees and slippage, the SPY benchmark, Monte Carlo and walk-forward checks, the exact rules and code behind each result, and the plain-English reading of it. A product that sells trust in tiers is selling something else.',
  },
  {
    q: 'What does Pro add?',
    a: 'Volume and convenience: unlimited backtests, more saved strategies, CSV export, and a weekly email on how your saved strategies have done.',
  },
  {
    q: 'How do I get Pro?',
    a: `The first ${GRANDFATHER_LIMIT} accounts are Pro for life, automatically. After that, Pro is set up by hand for now — get in touch.`,
  },
]

export function Pricing() {
  const navigate = useNavigate()
  // Day 12: no price renders unless the price flag is on (lib/pricing.ts).
  const price = proPrice()

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
            <Sparkles className="h-3 w-3" aria-hidden="true" /> The numbers are never paywalled
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="mt-6 text-4xl font-bold sm:text-5xl"
          >
            Free to start. <span className="text-gradient">Pro for more.</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-text-mid"
          >
            Every result, on every plan, comes with the same history, the same checks and the same
            honesty. Pro adds volume and convenience — never accuracy.
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

          <div className="relative grid gap-0 md:grid-cols-2">
            {/* Free */}
            <div className="p-8 sm:p-10">
              <div className="flex items-center gap-3">
                <h2 className="text-2xl font-bold">Free</h2>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-neon/25 bg-neon/10 px-3 py-1 text-2xs font-semibold text-neon">
                  <Heart className="h-3 w-3" aria-hidden="true" /> Free forever
                </span>
              </div>
              <p className="mt-4 max-w-sm text-sm leading-relaxed text-text-mid">
                The full product on real data, with every check on every result.
              </p>
              <Button size="lg" className="mt-6 w-full" onClick={() => navigate('/signup')}>
                Start for free <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Button>
              <p className="mt-8 text-2xs font-semibold uppercase tracking-wider text-text-low">
                Never paywalled, on any plan
              </p>
              <div className="mt-4 space-y-3">
                {NEVER_GATED.map((f) => (
                  <div key={f} className="flex items-start gap-3 text-sm text-text-hi">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gain/15">
                      <Check className="h-3 w-3 text-gain-soft" aria-hidden="true" />
                    </span>
                    {f}
                  </div>
                ))}
              </div>
            </div>

            {/* Side by side */}
            <div className="border-t border-white/[0.08] bg-ink-deep/40 p-8 sm:p-10 md:border-l md:border-t-0">
              <div className="flex items-center justify-between gap-3">
                <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
                  Free and Pro
                </p>
                {price ? (
                  <span className="font-mono text-sm font-semibold tabular text-text-hi">
                    Pro {price}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-2xs font-medium text-grape-soft">
                    <Gem className="h-3 w-3" aria-hidden="true" /> Pro by invitation for now
                  </span>
                )}
              </div>
              <table className="mt-5 w-full text-sm">
                <thead>
                  <tr className="text-left text-2xs uppercase tracking-wider text-text-low">
                    <th scope="col" className="pb-2 font-semibold">
                      <span className="sr-only">Capability</span>
                    </th>
                    <th scope="col" className="pb-2 font-semibold">
                      Free
                    </th>
                    <th scope="col" className="pb-2 font-semibold">
                      Pro
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {PLAN_ROWS.map((r) => (
                    <tr key={r.label}>
                      <th scope="row" className="py-2.5 pr-3 text-left font-normal text-text-mid">
                        {r.label}
                      </th>
                      <td className="py-2.5 pr-3 font-mono tabular text-text-hi">
                        {r.free === 'Unlimited' ? (
                          <span className="inline-flex items-center gap-1">
                            <InfinityIcon
                              className="h-3.5 w-3.5 text-gain-soft"
                              aria-hidden="true"
                            />
                            Unlimited
                          </span>
                        ) : (
                          r.free
                        )}
                      </td>
                      <td className="py-2.5 font-mono tabular text-text-hi">{r.pro}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="mt-8 rounded-xl border border-gain/20 bg-gain/[0.06] p-4">
                <p className="flex items-center gap-2 text-xs font-semibold text-gain-soft">
                  <Heart className="h-3.5 w-3.5" aria-hidden="true" /> Honest pricing note
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-text-mid">
                  The first {GRANDFATHER_LIMIT} accounts are Pro for life. Whatever Pro costs later,
                  nothing in the left-hand list will ever be part of it.
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
