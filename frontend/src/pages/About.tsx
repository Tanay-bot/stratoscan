import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Coins, Cpu, Lock, Radar, ShieldCheck, Timer } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { CountUp } from '@/components/landing/CountUp'
import { Button } from '@/components/ui/button'

const PILLARS = [
  {
    icon: Coins,
    title: 'Built to stay free',
    body: 'Terminal-grade speed and depth, with no subscription and no credit card. Free isn’t a promotion here — it’s the design.',
  },
  {
    icon: Timer,
    title: 'Free is the architecture',
    body: 'There is no paywall because there was never meant to be one. Free isn’t a trial or a teaser — it’s the product itself.',
  },
  {
    icon: Cpu,
    title: 'Resilient by design',
    body: 'Built to stay up when it matters. If a data source stumbles, you’ll never notice — the terminal keeps streaming and the news keeps flowing.',
  },
  {
    icon: Lock,
    title: 'Security as a habit',
    body: 'JWT auth with PBKDF2 hashing, locked-down CORS and a secrets runbook — security as a habit, not an afterthought.',
  },
]

const FACTS: { target: number; suffix: string; label: string }[] = [
  { target: 100, suffix: '%', label: 'free — no paywall' },
  { target: 1400, suffix: '+', label: 'US stocks in the universe' },
  { target: 10, suffix: 'y', label: 'of backtest history' },
  { target: 24, suffix: '/7', label: 'market coverage' },
  { target: 3, suffix: 's', label: 'from idea to ranked list' },
  { target: 10, suffix: '/day', label: 'backtests, free' },
]

export function About() {
  const navigate = useNavigate()

  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="grid-backdrop absolute inset-0" aria-hidden="true" />
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[560px] -translate-x-1/2 rounded-full bg-neon/[0.08] blur-[100px]"
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-3xl px-5 pb-16 pt-20 text-center">
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="text-4xl font-bold sm:text-5xl"
          >
            A market terminal that <span className="text-gradient">reads English</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-text-mid"
          >
            Stratoscan exists because the gap between an investing idea and a validated candidate
            list should be measured in seconds — not spreadsheets.
          </motion.p>

          {/* Stat band */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.2 }}
            className="mt-12 grid grid-cols-2 gap-x-6 gap-y-8 sm:grid-cols-3"
          >
            {FACTS.map((f) => (
              <div key={f.label}>
                <p className="font-mono text-3xl font-bold tabular text-neon">
                  <CountUp target={f.target} suffix={f.suffix} />
                </p>
                <p className="mt-1 text-xs text-text-low">{f.label}</p>
              </div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* Story */}
      <section className="mx-auto max-w-4xl px-5 py-16">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass rounded-3xl p-8 sm:p-10"
        >
          <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">
            Why it exists
          </p>
          <div className="mt-4 space-y-4 text-base leading-relaxed text-text-mid">
            <p>
              Most retail investors never get past the screener. The tools that would actually help
              — ranked candidate lists, honest backtests, benchmark comparison — are locked behind
              subscription tiers and spreadsheet-shaped interfaces.
            </p>
            <p>
              Stratoscan takes the opposite bet: a natural-language interpreter turns your idea into
              structured filters, ranks the universe in seconds, and validates the strategy on 10
              years of real bars. The product is free because it was built to be — a promise, not a
              promotion.
            </p>
            <p className="font-medium text-text-hi">
              Not a stock-picking service. A bridge from idea to candidate list — you still do the
              thinking.
            </p>
          </div>
        </motion.div>
      </section>

      {/* Pillars */}
      <section className="mx-auto max-w-7xl px-5 py-16">
        <div className="mb-12 text-center">
          <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">
            What we believe
          </p>
          <h2 className="mt-3 text-3xl font-bold sm:text-4xl">Four pillars</h2>
        </div>
        <div className="grid gap-6 sm:grid-cols-2">
          {PILLARS.map(({ icon: Icon, title, body }, i) => (
            <motion.div
              key={title}
              initial={{ opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.5, delay: i * 0.08 }}
              className="glass-hover rounded-2xl p-6"
            >
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-neon/15 to-grape/20">
                <Icon className="h-5 w-5 text-neon" aria-hidden="true" />
              </div>
              <h3 className="mt-4 text-base font-semibold">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-text-mid">{body}</p>
            </motion.div>
          ))}
        </div>
      </section>

      {/* Honesty strip */}
      <section className="mx-auto max-w-4xl px-5 pb-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass flex flex-wrap items-center gap-4 rounded-2xl p-6"
        >
          <ShieldCheck className="h-6 w-6 shrink-0 text-gain-soft" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-text-hi">The honesty clause</h3>
            <p className="mt-1 text-sm leading-relaxed text-text-mid">
              Every backtest shows its exact simulated rules, its data-quality warnings, and its SPY
              benchmark. If a result can't be trusted, we show you why.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-xl bg-white/[0.05] px-4 py-2.5">
            <Radar className="h-4 w-4 text-grape-soft" aria-hidden="true" />
            <span className="font-mono text-xs tabular text-text-hi">
              alpha vs benchmark: always shown
            </span>
          </div>
        </motion.div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-4xl px-5 py-20">
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass-strong rounded-3xl p-10 text-center shadow-card"
        >
          <h2 className="text-3xl font-bold">Ideas are cheap. Validation is work.</h2>
          <p className="mx-auto mt-3 max-w-md text-text-mid">
            Start with one sentence. Stratoscan handles the candidate list and the backtest.
          </p>
          <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" onClick={() => navigate('/signup')}>
              Get started free <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button size="lg" variant="outline" onClick={() => navigate('/pricing')}>
              See pricing
            </Button>
          </div>
        </motion.div>
      </section>

      <Footer />
    </div>
  )
}
