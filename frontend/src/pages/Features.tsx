import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  BarChart3,
  BrainCircuit,
  Check,
  Cpu,
  Heart,
  Radar,
  ShieldCheck,
} from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { Button } from '@/components/ui/button'

const GROUPS = [
  {
    id: '01',
    icon: BrainCircuit,
    name: 'Screening',
    tagline: 'Ask, don’t configure',
    rows: [
      {
        label: 'Natural-language interpreter',
        detail:
          'Plain-English queries parsed into explicit, reviewable criteria — sector, market cap, valuation, yield, momentum, trend.',
      },
      {
        label: 'Composite ranking',
        detail:
          'Value, momentum and quality factors fused into a single score; every result shows why it ranked where it did.',
      },
      {
        label: 'Saved screeners',
        detail:
          'Re-run any query in one click from the dashboard. Criteria persist between visits.',
      },
      {
        label: 'Filters drawer',
        detail: 'Tune results — sector, cap, P/E, yield — without leaving the results grid.',
      },
    ],
  },
  {
    id: '02',
    icon: BarChart3,
    name: 'Backtesting',
    tagline: '10 years, exactly simulated',
    rows: [
      {
        label: 'Adjusted daily history',
        detail:
          'Ten years of daily bars, split- and dividend-adjusted, cached per symbol and re-fetched whenever the cache does not cover the window you asked for.',
      },
      {
        label: 'Exact rule simulation',
        detail:
          'Stops, take-profit, position sizing and optional shorts are simulated exactly as written.',
      },
      {
        label: 'Monte Carlo spread',
        detail:
          'Outcome distribution and bootstrap confidence intervals on every run — not a single lucky path.',
      },
      {
        label: 'Walk-forward validation',
        detail:
          'In-sample / out-of-sample discipline, so results are judged like a real research process.',
      },
      {
        label: 'SPY benchmark',
        detail: 'Alpha is always measured against a benchmark, never implied.',
      },
    ],
  },
  {
    id: '03',
    icon: Radar,
    name: 'Intelligence',
    tagline: 'Signal on top of prices',
    rows: [
      {
        label: 'Sentiment pipeline',
        detail:
          'Headlines scored bullish / neutral / bearish per symbol by a language model, with failover across providers.',
      },
      {
        label: 'News feed',
        detail: 'A scrolling feed per symbol, filterable by sentiment, refreshed on a schedule.',
      },
      {
        label: 'Watchlist',
        detail: 'Track candidates and their latest price change from the dashboard.',
      },
    ],
  },
  {
    id: '04',
    icon: Cpu,
    name: 'Platform',
    tagline: 'Boring in the best way',
    rows: [
      {
        label: 'Security as a habit',
        detail:
          'JWT auth with PBKDF2 hashing, locked-down CORS and a secrets runbook — security as a habit, not an afterthought.',
      },
      {
        label: 'Rate limits that protect you',
        detail: 'Generous limits keep screens fast for everyone — your access stays free.',
      },
      {
        label: 'Honest output',
        detail:
          'Data-quality warnings surfaced, exact simulated rules shown, benchmark always visible.',
      },
    ],
  },
]

export function Features() {
  const navigate = useNavigate()

  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      {/* ── Hero ────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="grid-backdrop absolute inset-0" aria-hidden="true" />
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[560px] -translate-x-1/2 rounded-full bg-grape/[0.1] blur-[100px]"
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-3xl px-5 pb-16 pt-20 text-center">
          <motion.p
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon"
          >
            Features
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="mt-4 text-4xl font-bold sm:text-5xl"
          >
            The whole engine. <span className="text-gradient">Free. Forever.</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-text-mid"
          >
            The full specification of what ships in the free account — written down, so “free” means
            something specific. Pro adds volume and convenience, never accuracy.
          </motion.p>
        </div>
      </section>

      {/* ── Spec sheet ──────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl space-y-14 px-5 pb-16">
        {GROUPS.map(({ id, icon: Icon, name, tagline, rows }) => (
          <motion.div
            key={name}
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-60px' }}
            transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
          >
            {/* Group header */}
            <div className="mb-5 flex items-center gap-4">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-neon/15 to-grape/25 shadow-[inset_0_0_0_1px_rgba(0,212,255,0.2)]">
                <Icon className="h-5 w-5 text-neon" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <div className="flex items-baseline gap-3">
                  <h2 className="text-xl font-bold sm:text-2xl">{name}</h2>
                  <span className="hidden font-mono text-xs text-neon sm:inline">[{id}]</span>
                </div>
                <p className="mt-0.5 font-mono text-2xs uppercase tracking-wider text-text-low">
                  {tagline}
                </p>
              </div>
            </div>

            {/* Rows — terminal spec-sheet */}
            <div className="glass overflow-hidden rounded-2xl">
              {rows.map((row, ri) => (
                <div
                  key={row.label}
                  className={`grid gap-1 px-5 py-4 transition-colors hover:bg-white/[0.03] sm:grid-cols-[minmax(220px,0.9fr)_1.4fr] sm:gap-6 sm:px-6 ${
                    ri > 0 ? 'border-t border-white/[0.06]' : ''
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Check className="h-3.5 w-3.5 shrink-0 text-gain" aria-hidden="true" />
                    <p className="font-mono text-sm font-semibold text-text-hi">{row.label}</p>
                  </div>
                  <p className="text-sm leading-relaxed text-text-mid sm:pl-2">{row.detail}</p>
                </div>
              ))}
            </div>
          </motion.div>
        ))}
      </section>

      {/* ── Honesty strip ───────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-5 pb-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass flex flex-wrap items-center gap-4 rounded-2xl p-6"
        >
          <ShieldCheck className="h-6 w-6 shrink-0 text-gain-soft" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-text-hi">What this is not</h3>
            <p className="mt-1 text-sm leading-relaxed text-text-mid">
              Stratoscan is a research tool, not a stock-picking service. Backtests show what
              happened, with data-quality warnings and a benchmark — they never predict what will.
              You still do the thinking.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-xl bg-white/[0.05] px-4 py-2.5">
            <Heart className="h-4 w-4 text-grape-soft" aria-hidden="true" />
            <span className="font-mono text-xs tabular text-text-hi">free plan forever</span>
          </div>
        </motion.div>
      </section>

      {/* ── CTA ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-4xl px-5 py-20">
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass-strong rounded-3xl p-10 text-center shadow-card"
        >
          <h2 className="text-3xl font-bold">The whole spec sheet is your plan.</h2>
          <p className="mx-auto mt-3 max-w-md text-text-mid">
            Every feature above is in the free account. Pro adds unlimited backtests, CSV export and
            weekly strategy emails — never better numbers.
          </p>
          <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" onClick={() => navigate('/signup')}>
              Start screening free <ArrowRight className="h-4 w-4" aria-hidden="true" />
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
