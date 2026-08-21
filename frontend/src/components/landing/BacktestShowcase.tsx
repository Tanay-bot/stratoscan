import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, BarChart3, CheckCircle2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { CountUp } from '@/components/landing/CountUp'
import { makeSeries, seeded, toPath } from '@/lib/series'
import { useAuth } from '@/store/auth'

const REDUCED =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const BULLETS = [
  {
    t: 'Exact rules, not vibes',
    d: 'Stops, take-profit, position sizing, shorts — the strategy runs exactly as written.',
  },
  {
    t: 'Walk-forward honest',
    d: 'Replayed on 10 years of daily bars, never trained on the data it validates.',
  },
  {
    t: 'Measured against SPY',
    d: 'Monte Carlo spread plus the benchmark on every run — alpha is shown, not implied.',
  },
]

const TRADES = [
  { d: 'day 118', side: 'BUY', sym: 'SOUN', pnl: null, note: 'RSI 28 < 30' },
  { d: 'day 126', side: 'SELL', sym: 'SOUN', pnl: '+8.9%', note: 'take-profit hit' },
  { d: 'day 207', side: 'BUY', sym: 'CRDO', pnl: null, note: 'SMA-50 cross' },
  { d: 'day 214', side: 'SELL', sym: 'CRDO', pnl: '+11.2%', note: 'trailing stop' },
]

const KPIS = [
  { label: 'Total return', value: 38.2, prefix: '+', suffix: '%', decimals: 1 },
  { label: 'Sharpe', value: 1.24, prefix: '', suffix: '', decimals: 2 },
  { label: 'Max drawdown', value: 11.8, prefix: '−', suffix: '%', decimals: 1 },
  { label: 'Win rate', value: 57, prefix: '', suffix: '%', decimals: 0 },
]

export function BacktestShowcase() {
  const navigate = useNavigate()
  const { user } = useAuth()

  const { line, area, bench, endY } = useMemo(() => {
    const strat = makeSeries(seeded(7), 90, 0.0022, 0.05, 12, 78)
    const spy = makeSeries(seeded(3), 90, 0.0007, 0.018, 12, 78)
    const last = strat[strat.length - 1]
    return {
      line: toPath(strat),
      area: `${toPath(strat)} L100,86 L0,86 Z`,
      bench: toPath(spy),
      endY: last.y,
    }
  }, [])

  return (
    <section className="mx-auto max-w-7xl px-5 py-20">
      <div className="mb-14 max-w-2xl">
        <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">
          The backtester
        </p>
        <h2 className="mt-3 text-3xl font-bold sm:text-4xl">
          Backtest the thesis before you trust it.
        </h2>
        <p className="mt-4 text-text-mid">
          Every candidate list becomes a simulated portfolio — replayed over 10 years of daily bars
          with your exact rules, then judged against SPY.
        </p>
      </div>

      <div className="grid items-center gap-12 lg:grid-cols-[1fr_1.05fr]">
        {/* Copy + CTA */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        >
          <ul className="space-y-5">
            {BULLETS.map((b) => (
              <li key={b.t} className="flex items-start gap-4">
                <span className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-neon/20 to-grape/25 shadow-[inset_0_0_0_1px_rgba(0,212,255,0.2)]">
                  <CheckCircle2 className="h-4 w-4 text-neon" aria-hidden="true" />
                </span>
                <div>
                  <p className="font-medium text-text-hi">{b.t}</p>
                  <p className="mt-0.5 text-sm text-text-mid">{b.d}</p>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Button
              size="lg"
              onClick={() => navigate(user ? '/app/backtest' : '/login?next=/app/backtest')}
            >
              Open the backtester <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button size="lg" variant="outline" onClick={() => navigate('/how-it-works')}>
              See how it works
            </Button>
          </div>
        </motion.div>

        {/* Animated run card */}
        <motion.div
          initial={{ opacity: 0, y: 28, rotate: 0.5 }}
          whileInView={{ opacity: 1, y: 0, rotate: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.7, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
          className="relative"
        >
          <div
            className="pointer-events-none absolute -inset-5 rounded-3xl bg-gradient-to-br from-grape/10 via-transparent to-neon/10 blur-2xl"
            aria-hidden="true"
          />

          <div className="glass-strong relative overflow-hidden rounded-3xl shadow-card">
            {/* Chrome */}
            <div className="flex items-center gap-2 border-b border-white/[0.08] px-5 py-3">
              <span className="h-2.5 w-2.5 rounded-full bg-loss/80" aria-hidden="true" />
              <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" aria-hidden="true" />
              <span className="h-2.5 w-2.5 rounded-full bg-gain/80" aria-hidden="true" />
              <span className="ml-3 font-mono text-xs text-text-low">
                backtest — momentum · small caps
              </span>
              <span className="ml-auto flex items-center gap-1.5 rounded-full border border-gain/25 bg-gain/10 px-2.5 py-0.5 text-2xs font-semibold text-gain-soft">
                <span
                  className="h-1.5 w-1.5 animate-pulse rounded-full bg-gain"
                  aria-hidden="true"
                />{' '}
                DONE
              </span>
            </div>

            {/* Progress */}
            <div className="border-b border-white/[0.08] px-5 pb-4 pt-4">
              <div className="flex items-center justify-between font-mono text-2xs text-text-low">
                <span>simulating 2,415 trading days…</span>
                <span>walk-forward · 4 folds</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.07]">
                <motion.div
                  initial={{ width: REDUCED ? '100%' : '0%' }}
                  whileInView={{ width: '100%' }}
                  viewport={{ once: true, margin: '-60px' }}
                  transition={{ duration: 2.4, ease: 'easeInOut' }}
                  className="h-full rounded-full bg-gradient-to-r from-neon to-grape"
                />
              </div>
            </div>

            {/* Equity curve */}
            <div className="px-5 pt-4">
              <div className="relative overflow-hidden rounded-2xl border border-white/[0.07] bg-ink-deep/50">
                <svg
                  viewBox="0 0 100 86"
                  preserveAspectRatio="none"
                  className="h-44 w-full"
                  role="img"
                  aria-label="Illustrative equity curve versus SPY benchmark"
                >
                  <defs>
                    <linearGradient id="btArea" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.22" />
                      <stop offset="100%" stopColor="#00d4ff" stopOpacity="0" />
                    </linearGradient>
                    <linearGradient id="btLine" x1="0" y1="0" x2="1" y2="0">
                      <stop offset="0%" stopColor="#00d4ff" />
                      <stop offset="100%" stopColor="#a78bfa" />
                    </linearGradient>
                  </defs>

                  {/* Benchmark (dashed, drawn first) */}
                  <motion.path
                    d={bench}
                    fill="none"
                    stroke="#9aa3b8"
                    strokeOpacity="0.55"
                    strokeWidth="0.7"
                    strokeDasharray="2 2"
                    initial={REDUCED ? { opacity: 0.55 } : { pathLength: 1, opacity: 0 }}
                    whileInView={{ pathLength: 1, opacity: 0.55 }}
                    viewport={{ once: true, margin: '-60px' }}
                    transition={{ duration: 1.2, ease: 'easeOut' }}
                  />

                  {/* Area fill */}
                  <motion.path
                    d={area}
                    fill="url(#btArea)"
                    initial={REDUCED ? { opacity: 1 } : { opacity: 0 }}
                    whileInView={{ opacity: 1 }}
                    viewport={{ once: true, margin: '-60px' }}
                    transition={{ duration: 0.8, delay: 1.4 }}
                  />

                  {/* Strategy line */}
                  <motion.path
                    d={line}
                    fill="none"
                    stroke="url(#btLine)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    initial={REDUCED ? { pathLength: 1 } : { pathLength: 0 }}
                    whileInView={{ pathLength: 1 }}
                    viewport={{ once: true, margin: '-60px' }}
                    transition={{ duration: 2, delay: 0.5, ease: 'easeInOut' }}
                  />

                  {/* End-point pulse */}
                  {!REDUCED && (
                    <motion.circle
                      cx="100"
                      cy={endY}
                      r="1.4"
                      fill="#00d4ff"
                      initial={{ opacity: 0 }}
                      whileInView={{ opacity: [0, 1, 0.4, 1] }}
                      viewport={{ once: true, margin: '-60px' }}
                      transition={{ duration: 1.4, delay: 2.3, repeat: Infinity, repeatDelay: 1.6 }}
                      style={{ filter: 'drop-shadow(0 0 3px rgba(0,212,255,0.9))' }}
                    />
                  )}
                </svg>
                <div className="absolute left-3 top-2.5 flex items-center gap-3 font-mono text-2xs text-text-low">
                  <span className="flex items-center gap-1.5">
                    <span
                      className="h-[2px] w-4 rounded-full bg-gradient-to-r from-neon to-grape"
                      aria-hidden="true"
                    />{' '}
                    strategy
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span
                      className="h-0 w-4 border-t border-dashed border-text-low"
                      aria-hidden="true"
                    />{' '}
                    SPY
                  </span>
                </div>
              </div>
            </div>

            {/* KPIs */}
            <div className="grid grid-cols-2 gap-2 px-5 py-4 sm:grid-cols-4">
              {KPIS.map((k) => (
                <div
                  key={k.label}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.03] px-2 py-2.5 text-center"
                >
                  <p className="font-mono text-base font-bold tabular text-text-hi sm:text-lg">
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
            </div>

            {/* Trade log */}
            <div className="border-t border-white/[0.08] px-5 py-4">
              <div className="mb-3 flex items-center justify-between">
                <p className="font-mono text-2xs uppercase tracking-wider text-text-low">
                  trade log
                </p>
                <p className="font-mono text-2xs text-text-low">10y · daily bars</p>
              </div>
              <div className="space-y-1.5 font-mono text-2xs">
                {TRADES.map((t, i) => (
                  <motion.div
                    key={`${t.side}-${t.sym}-${i}`}
                    initial={REDUCED ? { opacity: 1, x: 0 } : { opacity: 0, x: 12 }}
                    whileInView={{ opacity: 1, x: 0 }}
                    viewport={{ once: true, margin: '-40px' }}
                    transition={{ duration: 0.4, delay: 1.2 + i * 0.22 }}
                    className="flex items-center gap-3 rounded-lg bg-white/[0.03] px-3 py-1.5"
                  >
                    <span className="text-text-low">{t.d}</span>
                    <span className={t.side === 'BUY' ? 'text-gain-soft' : 'text-loss-soft'}>
                      {t.side}
                    </span>
                    <span className="font-bold text-text-hi">{t.sym}</span>
                    <span className="flex-1 text-text-low">{t.note}</span>
                    <span
                      className={
                        t.pnl && t.pnl.startsWith('+') ? 'text-gain-soft' : 'text-text-low'
                      }
                    >
                      {t.pnl ?? '—'}
                    </span>
                  </motion.div>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-between border-t border-white/[0.08] px-5 py-3">
              <p className="font-mono text-2xs text-text-low">illustrative simulated performance</p>
              <div className="flex items-center gap-1.5 text-2xs text-text-low">
                <BarChart3 className="h-3 w-3" aria-hidden="true" /> 4/4 folds complete
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
