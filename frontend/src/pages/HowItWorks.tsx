import { useState } from 'react'
import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, BarChart3, BrainCircuit, Check, ChevronRight, Search } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { BacktestLoop } from '@/components/landing/BacktestLoop'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface Step {
  n: string
  icon: typeof BrainCircuit
  title: string
  lead: string
  body?: string
  points?: string[]
  example?: string[]
  quote?: string
  visual?: 'results'
}

/** Mock screenshot of the screener results grid with a backtest CTA */
function ResultsMock() {
  const navigate = useNavigate()
  const rows = [
    { r: 1, sym: 'SOUN', name: 'SoundHound AI', price: '6.81', chg: '+12.4%', score: 92 },
    { r: 2, sym: 'CRDO', name: 'Credo Technology', price: '61.20', chg: '+8.1%', score: 88 },
    { r: 3, sym: 'ALAB', name: 'Astera Labs', price: '92.41', chg: '+5.6%', score: 84 },
    { r: 4, sym: 'AVGO', name: 'Broadcom', price: '178.44', chg: '+3.2%', score: 79 },
    { r: 5, sym: 'QTWO', name: 'Q2 Holdings', price: '104.10', chg: '-1.4%', score: 76 },
  ]
  return (
    <div className="glass overflow-hidden rounded-2xl">
      <div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-3">
        <p className="font-mono text-2xs uppercase tracking-wider text-text-low">
          results · momentum · small caps
        </p>
        <span className="rounded-full border border-gain/25 bg-gain/10 px-2 py-0.5 text-2xs font-semibold text-gain-soft">
          10 matches
        </span>
      </div>
      <table className="w-full font-mono text-xs tabular">
        <thead>
          <tr className="border-b border-white/[0.06] text-2xs uppercase tracking-wider text-text-low">
            <th className="px-5 py-2.5 text-left font-medium">#</th>
            <th className="px-2 py-2.5 text-left font-medium">Symbol</th>
            <th className="px-2 py-2.5 text-left font-medium">Company</th>
            <th className="px-2 py-2.5 text-right font-medium">Price</th>
            <th className="px-2 py-2.5 text-right font-medium">Chg</th>
            <th className="px-5 py-2.5 text-right font-medium">Score</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.sym}
              className="border-b border-white/[0.04] transition-colors last:border-0 hover:bg-white/[0.03]"
            >
              <td className="px-5 py-2.5 text-text-low">{r.r}</td>
              <td className="px-2 py-2.5 font-bold text-text-hi">{r.sym}</td>
              <td className="px-2 py-2.5 font-sans text-text-mid">{r.name}</td>
              <td className="px-2 py-2.5 text-right text-text-hi">${r.price}</td>
              <td
                className={cn(
                  'px-2 py-2.5 text-right',
                  r.chg.startsWith('-') ? 'text-loss-soft' : 'text-gain-soft',
                )}
              >
                {r.chg}
              </td>
              <td className="px-5 py-2.5 text-right">
                <span className="rounded-md bg-neon/15 px-1.5 py-0.5 font-bold text-neon">
                  {r.score}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center justify-between gap-3 border-t border-white/[0.08] bg-white/[0.02] px-5 py-3">
        <p className="font-mono text-2xs text-text-low">illustrative output</p>
        <Button size="sm" variant="grape" onClick={() => navigate('/signup')}>
          Backtest strategy <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}

const EXAMPLES = [
  {
    query: 'find undervalued small-cap tech stocks',
    criteria: [
      { k: 'sector', v: 'Technology' },
      { k: 'market cap', v: '< $2B' },
      { k: 'valuation', v: 'P/E < 20 · EV/EBITDA < 15' },
      { k: 'rank by', v: 'composite score' },
    ],
  },
  {
    query: 'high-dividend energy companies',
    criteria: [
      { k: 'sector', v: 'Energy' },
      { k: 'dividend yield', v: '> 4%' },
      { k: 'quality', v: 'payout < 80% · 5y growth' },
      { k: 'rank by', v: 'yield × quality' },
    ],
  },
  {
    query: 'momentum breakout stocks',
    criteria: [
      { k: 'momentum', v: '20-day return > 10%' },
      { k: 'trend', v: 'price > SMA-50 > SMA-200' },
      { k: 'volume', v: 'relative volume > 1.5' },
      { k: 'rank by', v: 'momentum composite' },
    ],
  },
]

const STEPS: Step[] = [
  {
    n: '01',
    icon: BrainCircuit,
    title: 'Describe your idea',
    lead: 'Type the idea the way you think it — not a query language.',
    body: 'The interpreter reads sector, market cap, valuation, yield, momentum and trend language straight from your sentence. Before anything runs, it shows you exactly what it heard, as explicit criteria you can review and trust.',
    points: [
      'Plain English — no filters to configure',
      'Criteria extracted and made explicit',
      'You see the machine’s reading before it acts',
    ],
  },
  {
    n: '02',
    icon: Search,
    title: 'Screen the universe',
    lead: 'Every screen runs in seconds against the full US universe.',
    body: 'Your criteria are applied to every stock at once, and the results are ranked by a multi-factor composite across value, momentum and quality. No spreadsheets, no manual filtering — just the candidates.',
    points: [
      'Plain English in, ranked list out',
      'About 5,000 US stocks (and 45 ETFs) ranked in about a second',
      'Saved screeners re-run in one click',
    ],
  },
  {
    n: '03',
    icon: BarChart3,
    title: 'Get Candidates + Validation',
    lead: 'See a ranked list of matching stocks with their latest prices (refreshed every 15 minutes) — then backtest the strategy to see whether it has ever worked.',
    example: ['Results: 10 stocks', 'Click “Backtest”', 'See simulated performance'],
    quote:
      'Unlike traditional screeners that make you choose filters, Stratoscan translates your thesis into criteria, surfaces candidates, and validates the strategy — so you know if it would have worked.',
    visual: 'results',
  },
]

export function HowItWorks() {
  const navigate = useNavigate()
  const [active, setActive] = useState(0)

  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      {/* ── Hero ────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="grid-backdrop absolute inset-0" aria-hidden="true" />
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[560px] -translate-x-1/2 rounded-full bg-neon/[0.08] blur-[100px]"
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-3xl px-5 pb-16 pt-20 text-center">
          <motion.p
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon"
          >
            How it works
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="mt-4 text-4xl font-bold sm:text-5xl"
          >
            From <span className="text-gradient">thought</span> to thesis
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-text-mid"
          >
            Three steps separate an investing idea from a validated candidate list. Stratoscan does
            the heavy lifting between them — you stay in the driver’s seat.
          </motion.p>
        </div>
      </section>

      {/* ── Interactive interpreter demo ────────────────────────────── */}
      <section className="mx-auto max-w-4xl px-5 py-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass-strong overflow-hidden rounded-3xl shadow-card"
        >
          <div className="border-b border-white/[0.06] px-6 py-4">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" aria-hidden="true" />
              <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" aria-hidden="true" />
              <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" aria-hidden="true" />
              <span className="ml-3 font-mono text-xs text-text-low">the-interpreter</span>
            </div>
          </div>

          {/* Query tabs */}
          <div
            className="flex gap-2 overflow-x-auto px-6 pt-5 no-scrollbar"
            role="tablist"
            aria-label="Example queries"
          >
            {EXAMPLES.map((ex, i) => (
              <button
                key={ex.query}
                role="tab"
                id={`interpreter-tab-${i}`}
                aria-controls={`interpreter-panel`}
                aria-selected={active === i}
                onClick={() => setActive(i)}
                className={cn(
                  'shrink-0 rounded-lg px-3.5 py-2 text-left font-mono text-xs transition-colors',
                  active === i
                    ? 'bg-neon/15 text-neon shadow-[inset_0_0_0_1px_rgba(0,212,255,0.3)]'
                    : 'bg-white/[0.04] text-text-mid hover:text-text-hi',
                )}
              >
                &gt; {ex.query}
              </button>
            ))}
          </div>

          {/* Criteria breakdown */}
          <div
            className="px-6 pb-8 pt-5"
            role="tabpanel"
            id="interpreter-panel"
            aria-labelledby="interpreter-tab-0"
          >
            <div className="flex items-start gap-3 font-mono text-sm">
              <span className="mt-1 text-neon">&gt;</span>
              <p className="text-text-hi">{EXAMPLES[active].query}</p>
            </div>
            <div className="my-5 ml-6 border-l border-white/10 pl-5">
              <p className="font-mono text-2xs uppercase tracking-wider text-text-low">
                interpreter →
              </p>
              <div className="mt-4 flex flex-wrap gap-2.5">
                {EXAMPLES[active].criteria.map((c, i) => (
                  <motion.span
                    key={c.k}
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: i * 0.1, duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                    className="flex items-center gap-2 rounded-lg border border-grape/25 bg-grape/10 px-3 py-1.5 font-mono text-xs"
                  >
                    <span className="text-grape-soft">{c.k}</span>
                    <span className="text-text-low">=</span>
                    <span className="text-text-hi">{c.v}</span>
                  </motion.span>
                ))}
              </div>
            </div>
            <div className="ml-6 flex items-center gap-2 text-2xs text-text-low">
              <Check className="h-3.5 w-3.5 text-gain" aria-hidden="true" />
              Everything above is shown to you before the screen runs — no black box.
            </div>
          </div>
        </motion.div>
      </section>

      {/* ── The three steps ─────────────────────────────────────────── */}
      <section className="mx-auto max-w-6xl px-5 py-20">
        <div className="mb-14 text-center">
          <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">The loop</p>
          <h2 className="mt-3 text-3xl font-bold sm:text-4xl">Three steps, no learning curve</h2>
        </div>

        <div className="space-y-16">
          {STEPS.map((step, i) => {
            const Icon = step.icon
            return (
              <div key={step.n} className="grid items-center gap-10 lg:grid-cols-2">
                {/* Copy */}
                <motion.div
                  initial={{ opacity: 0, x: i % 2 === 0 ? -24 : 24 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, margin: '-80px' }}
                  transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                  className={cn(i % 2 === 1 && 'lg:order-2')}
                >
                  <div className="flex items-center gap-4">
                    <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-neon/20 to-grape/25 shadow-[inset_0_0_0_1px_rgba(0,212,255,0.2)]">
                      <Icon className="h-5 w-5 text-neon" aria-hidden="true" />
                    </span>
                    <span className="font-mono text-2xs font-semibold uppercase tracking-wider text-neon">
                      Step {step.n}
                    </span>
                  </div>
                  <h3 className="mt-5 text-2xl font-bold sm:text-3xl">{step.title}</h3>
                  <p className="mt-3 text-base font-medium text-text-hi">{step.lead}</p>
                  {step.body && (
                    <p className="mt-3 max-w-lg text-sm leading-relaxed text-text-mid">
                      {step.body}
                    </p>
                  )}

                  {step.example &&
                    (() => {
                      const parts = step.example
                      return (
                        <div className="mt-5 flex flex-wrap items-center gap-2 font-mono text-xs text-text-mid">
                          {parts.map((ex, i) => (
                            <span key={ex} className="flex items-center gap-2">
                              {i > 0 && <span aria-hidden="true">→</span>}
                              <span
                                className={cn(
                                  'rounded-md px-2 py-1',
                                  i === parts.length - 1
                                    ? 'bg-gain/10 text-gain-soft'
                                    : 'bg-white/[0.05]',
                                )}
                              >
                                {ex}
                              </span>
                            </span>
                          ))}
                        </div>
                      )
                    })()}

                  {step.quote && (
                    <div className="mt-6 max-w-lg rounded-2xl border border-grape/25 bg-grape/10 p-5">
                      <p className="font-mono text-2xs font-semibold uppercase tracking-wider text-grape-soft">
                        The key difference
                      </p>
                      <p className="mt-2 text-sm leading-relaxed text-text-hi">“{step.quote}”</p>
                    </div>
                  )}
                </motion.div>

                {/* Panel: points list or results table */}
                <motion.div
                  initial={{ opacity: 0, x: i % 2 === 0 ? 24 : -24 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, margin: '-80px' }}
                  transition={{ duration: 0.6, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
                  className={cn(i % 2 === 1 && 'lg:order-1')}
                >
                  {step.visual === 'results' ? (
                    <ResultsMock />
                  ) : (
                    <div className="glass rounded-2xl p-6">
                      <p className="font-mono text-2xs uppercase tracking-wider text-text-low">
                        {step.n === '01' && 'what the machine hears'}
                        {step.n === '02' && 'what the screen does'}
                      </p>
                      <ul className="mt-5 space-y-4">
                        {step.points?.map((p) => (
                          <li key={p} className="flex items-start gap-3">
                            <ChevronRight
                              className="mt-0.5 h-4 w-4 shrink-0 text-neon"
                              aria-hidden="true"
                            />
                            <span className="text-sm text-text-mid">{p}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </motion.div>
              </div>
            )
          })}
        </div>
      </section>

      <BacktestLoop />

      {/* ── CTA ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-4xl px-5 py-20">
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass-strong rounded-3xl p-10 text-center shadow-card"
        >
          <h2 className="text-3xl font-bold">One sentence. Three steps. Zero cost.</h2>
          <p className="mx-auto mt-3 max-w-md text-text-mid">
            Run the loop yourself and see your first ranked candidate list in seconds.
          </p>
          <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" onClick={() => navigate('/signup')}>
              Get started free <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button size="lg" variant="outline" onClick={() => navigate('/features')}>
              See the features
            </Button>
          </div>
        </motion.div>
      </section>

      <Footer />
    </div>
  )
}
