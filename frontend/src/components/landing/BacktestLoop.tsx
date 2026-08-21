import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Gauge, History, SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CountUp } from '@/components/landing/CountUp'
import { makeSeries, seeded, toPath } from '@/lib/series'
import { useAuth } from '@/store/auth'

const REDUCED =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const STEPS = [
  {
    n: '01',
    icon: SlidersHorizontal,
    title: 'Define the strategy',
    lead: 'Build the exact rules before any bar is touched.',
    body: 'Compose conditions the way you think about the market — RSI thresholds, SMA crossovers, stops, take-profit and position sizing. Everything is explicit and editable.',
    chips: [
      'RSI < 30 → buy',
      'SMA-50 > SMA-200',
      'stop-loss −8%',
      'take-profit +20%',
      'position 10%',
    ],
  },
  {
    n: '02',
    icon: History,
    title: 'Replay 10 years',
    lead: 'The rules run against daily bars with walk-forward discipline.',
    body: 'Each fold trains on the past and validates on the unseen — the same strategy that would have run in real time. Monte Carlo replays it hundreds of times.',
    curve: true,
  },
  {
    n: '03',
    icon: Gauge,
    title: 'Read the verdict',
    lead: 'A scorecard, not a vibe.',
    body: 'Total return, Sharpe, max drawdown and win rate — measured against SPY on every run. Alpha is shown, never implied.',
    kpis: true,
  },
]

const KPIS = [
  { label: 'Total return', value: 38.2, prefix: '+', suffix: '%', decimals: 1 },
  { label: 'Sharpe', value: 1.24, prefix: '', suffix: '', decimals: 2 },
  { label: 'Max drawdown', value: 11.8, prefix: '−', suffix: '%', decimals: 1 },
  { label: 'Win rate', value: 57, prefix: '', suffix: '%', decimals: 0 },
]

function MiniCurve() {
  const { line, area, bench } = useMemo(() => {
    const strat = makeSeries(seeded(11), 60, 0.0021, 0.05, 10, 74)
    const spy = makeSeries(seeded(5), 60, 0.0007, 0.016, 10, 74)
    return {
      line: toPath(strat),
      area: `${toPath(strat)} L100,80 L0,80 Z`,
      bench: toPath(spy),
    }
  }, [])

  return (
    <div className="relative mt-5 h-28 overflow-hidden rounded-xl border border-white/[0.07] bg-ink-deep/50">
      <svg
        viewBox="0 0 100 80"
        preserveAspectRatio="none"
        className="h-full w-full"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="blArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.2" />
            <stop offset="100%" stopColor="#00d4ff" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="blLine" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#00d4ff" />
            <stop offset="100%" stopColor="#a78bfa" />
          </linearGradient>
        </defs>
        <motion.path
          d={bench}
          fill="none"
          stroke="#9aa3b8"
          strokeOpacity="0.5"
          strokeWidth="0.6"
          strokeDasharray="2 2"
          initial={REDUCED ? { pathLength: 1, opacity: 0.5 } : { pathLength: 1, opacity: 0 }}
          whileInView={{ opacity: 0.5 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.8 }}
        />
        <motion.path
          d={area}
          fill="url(#blArea)"
          initial={REDUCED ? { opacity: 1 } : { opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 0.6, delay: 0.9 }}
        />
        <motion.path
          d={line}
          fill="none"
          stroke="url(#blLine)"
          strokeWidth="1.4"
          strokeLinecap="round"
          initial={REDUCED ? { pathLength: 1 } : { pathLength: 0 }}
          whileInView={{ pathLength: 1 }}
          viewport={{ once: true, margin: '-40px' }}
          transition={{ duration: 1.5, ease: 'easeInOut' }}
        />
      </svg>
      <div className="absolute left-2.5 top-2 flex items-center gap-2.5 font-mono text-2xs text-text-low">
        <span className="flex items-center gap-1">
          <span
            className="h-[2px] w-3.5 rounded-full bg-gradient-to-r from-neon to-grape"
            aria-hidden="true"
          />{' '}
          strategy
        </span>
        <span className="flex items-center gap-1">
          <span className="h-0 w-3.5 border-t border-dashed border-text-low" aria-hidden="true" />{' '}
          SPY
        </span>
      </div>
      <div className="absolute bottom-2 right-2.5 font-mono text-2xs text-text-low">
        walk-forward · 4 folds
      </div>
    </div>
  )
}

export function BacktestLoop() {
  const navigate = useNavigate()
  const { user } = useAuth()

  return (
    <section className="mx-auto max-w-7xl px-5 py-20">
      <div className="mb-14 max-w-2xl">
        <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-grape-soft">
          The validation loop
        </p>
        <h2 className="mt-3 text-3xl font-bold sm:text-4xl">
          Candidates are a hypothesis. Backtest is the verdict.
        </h2>
        <p className="mt-4 text-text-mid">
          The same three steps power the backtester — build the rules, replay the years, read the
          scorecard.
        </p>
      </div>

      <div className="relative">
        {/* Animated connector */}
        <div
          className="pointer-events-none absolute left-0 right-0 top-6 hidden h-px lg:block"
          aria-hidden="true"
        >
          <div className="h-px w-full bg-gradient-to-r from-grape/40 via-neon/40 to-grape/40" />
          <motion.div
            className="absolute -top-[3px] h-[7px] w-[7px] rounded-full bg-grape shadow-glow-sm"
            animate={{ left: ['0%', '100%', '0%'] }}
            transition={{ duration: 10, repeat: Infinity, ease: 'easeInOut' }}
          />
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          {STEPS.map((step, i) => {
            const Icon = step.icon
            return (
              <motion.div
                key={step.n}
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-80px' }}
                transition={{ duration: 0.55, delay: i * 0.14, ease: [0.16, 1, 0.3, 1] }}
                className="glass-hover relative rounded-2xl p-6"
              >
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-grape/25 to-neon/20 shadow-[inset_0_0_0_1px_rgba(124,58,237,0.25)]">
                  <Icon className="h-5 w-5 text-grape-soft" aria-hidden="true" />
                </div>
                <p className="mt-5 font-mono text-2xs font-semibold uppercase tracking-wider text-grape-soft">
                  BT-{step.n}
                </p>
                <h3 className="mt-2 text-lg font-semibold">{step.title}</h3>
                <p className="mt-1.5 text-sm font-medium text-text-hi">{step.lead}</p>
                <p className="mt-2 text-sm leading-relaxed text-text-mid">{step.body}</p>

                {step.chips && (
                  <div className="mt-5 flex flex-wrap gap-2">
                    {step.chips.map((c) => (
                      <span
                        key={c}
                        className="rounded-lg border border-grape/25 bg-grape/10 px-2.5 py-1 font-mono text-2xs text-text-hi"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                )}
                {step.curve && <MiniCurve />}
                {step.kpis && (
                  <div className="mt-5 grid grid-cols-2 gap-2">
                    {KPIS.map((k) => (
                      <div
                        key={k.label}
                        className="rounded-xl border border-white/[0.06] bg-white/[0.03] px-2 py-2.5 text-center"
                      >
                        <p className="font-mono text-base font-bold tabular text-text-hi">
                          <CountUp
                            target={k.value}
                            prefix={k.prefix}
                            suffix={k.suffix}
                            decimals={k.decimals}
                          />
                        </p>
                        <p className="mt-0.5 text-2xs text-text-low">{k.label}</p>
                      </div>
                    ))}
                    <p className="col-span-2 pt-1 text-center font-mono text-2xs text-text-low">
                      vs SPY · illustrative
                    </p>
                  </div>
                )}
              </motion.div>
            )
          })}
        </div>
      </div>

      <div className="mt-12 flex flex-col items-center justify-center gap-4 sm:flex-row">
        <Button
          size="lg"
          variant="grape"
          onClick={() => navigate(user ? '/app/backtest' : '/login?next=/app/backtest')}
        >
          Open the backtester <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
        <button
          onClick={() => navigate('/signup')}
          className="group flex items-center gap-2 text-sm font-medium text-text-mid transition-colors hover:text-text-hi"
        >
          Try the whole loop yourself
          <ArrowRight
            className="h-4 w-4 transition-transform group-hover:translate-x-1"
            aria-hidden="true"
          />
        </button>
      </div>
    </section>
  )
}
