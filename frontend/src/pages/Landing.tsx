import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'
import { ArrowRight, Search, Sparkles } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { HeroViz } from '@/components/landing/HeroViz'
import { TerminalHero } from '@/components/landing/TerminalHero'
import { Pipeline } from '@/components/landing/Pipeline'
import { BacktestShowcase } from '@/components/landing/BacktestShowcase'
import { CountUp } from '@/components/landing/CountUp'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/store/auth'
import { DEMO_MODE } from '@/lib/api/client'
import { track } from '@/lib/analytics'
import { templateById, type StrategyTemplate } from '@/lib/templates'
import { useToasts } from '@/store/notifications'
import { useRegisterSearchInput } from '@/hooks/useKeyboardShortcuts'

// Hero chips are strategy templates (Day 8): each parses on the free path and
// backtests sanely. A signed-out click lands in a FREE trial run, so a chip
// that churned or matched nothing would spend one of three runs on a bad
// first impression.
const HERO_TEMPLATES = (
  ['rsi-pullback-uptrend', 'oversold-mean-reversion', 'dividend-durability'] as const
)
  .map((id) => templateById(id))
  .filter((t): t is StrategyTemplate => Boolean(t))

const MARQUEE = [
  'your idea, in plain english',
  '10 years of daily prices',
  'fees + slippage on both legs',
  'filled at the next open',
  'monte carlo',
  'walk-forward windows',
  'spy benchmark',
  'every number re-derived',
]

// The animated placeholder is also what "Screen" submits when nothing was
// typed — and signed out, that is a free trial backtest. So it cycles through
// template queries, which parse for free and backtest sanely.
const SEARCH_PROMPTS = (
  [
    'oversold-mean-reversion',
    'utility-income-dip',
    'healthcare-oversold',
    'mega-cap-defensive',
  ] as const
)
  .map((id) => templateById(id)?.query)
  .filter((q): q is string => Boolean(q))

/**
 * Types, holds, deletes, then types the next prompt — a rotating search demo.
 * Runs even under prefers-reduced-motion: it's a character-by-character text
 * reveal (the text doesn't move), so it's the one "animation" we keep on.
 * The heavy hero motion (tape slide, particles, flashes) still respects REDUCED.
 */
function useRotatingPrompts(prompts: string[], active: boolean) {
  const [text, setText] = useState('')
  const idx = useRef(0)

  useEffect(() => {
    if (!active) return
    let i = 0
    let deleting = false
    let timer: ReturnType<typeof setTimeout>
    const step = () => {
      const phrase = prompts[idx.current % prompts.length]
      if (!deleting) {
        i++
        setText(phrase.slice(0, i))
        if (i >= phrase.length) {
          deleting = true
          timer = setTimeout(step, 2400) // hold the full prompt
          return
        }
        timer = setTimeout(step, 38)
      } else {
        i--
        setText(phrase.slice(0, i))
        if (i <= 0) {
          deleting = false
          idx.current++
          timer = setTimeout(step, 500)
          return
        }
        timer = setTimeout(step, 15)
      }
    }
    timer = setTimeout(step, 800)
    return () => clearTimeout(timer)
  }, [active, prompts])

  const full = prompts[idx.current % prompts.length]
  return { text, full }
}

export function Landing() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const toasts = useToasts()
  const searchRef = useRegisterSearchInput(1)
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const [edited, setEdited] = useState(false)
  const { text: auto, full } = useRotatingPrompts(SEARCH_PROMPTS, !focused && !edited)
  const shown = edited ? query : focused ? '' : auto

  // The top of the funnel. Mount-only (""), not on every re-render: this is
  // the denominator for every other event, so it must count visits and not
  // the interactions that happen during one.
  useEffect(() => {
    track('landing_view')
  }, [])

  const runSearch = (q: string) => {
    const trimmed = q.trim()
    if (!trimmed) {
      toasts.push('info', 'Type an idea to test', 'Or try one of the examples below.')
      return
    }
    // The pitch is testing ideas (Day 14), so the idea goes straight into a
    // backtest. Signed out that is a free trial run (Day 6: no signup wall) —
    // the account ask comes after the result, never before.
    navigate(
      user
        ? `/app/backtest?q=${encodeURIComponent(trimmed)}`
        : `/try?q=${encodeURIComponent(trimmed)}`,
    )
  }

  const submit = () => runSearch(edited ? shown : full)

  // Mouse parallax on hero glows
  const mx = useMotionValue(0)
  const my = useMotionValue(0)
  const sx = useSpring(mx, { stiffness: 60, damping: 20 })
  const sy = useSpring(my, { stiffness: 60, damping: 20 })
  const glowX = useTransform(sx, [-0.5, 0.5], [-40, 40])
  const glowY = useTransform(sy, [-0.5, 0.5], [-30, 30])

  return (
    <div
      id="main-content"
      tabIndex={-1}
      className="min-h-screen overflow-x-hidden outline-none"
      onMouseMove={(e) => {
        mx.set(e.clientX / window.innerWidth - 0.5)
        my.set(e.clientY / window.innerHeight - 0.5)
      }}
    >
      <LandingNav />

      {/* ── Hero ────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0">
          <HeroViz />
          <motion.div
            style={{ x: glowX, y: glowY }}
            className="absolute -top-20 right-[8%] h-72 w-72 rounded-full bg-grape/[0.14] blur-[90px]"
            aria-hidden="true"
          />
          <motion.div
            style={{ x: glowX, y: glowY }}
            className="absolute bottom-0 left-[4%] h-64 w-64 rounded-full bg-neon/[0.09] blur-[80px]"
            aria-hidden="true"
          />
        </div>

        <div className="relative mx-auto grid max-w-7xl items-center gap-12 px-5 pb-20 pt-16 lg:grid-cols-[1.05fr_0.95fr] lg:pb-28 lg:pt-24">
          {/* Left: copy + search */}
          <div className="max-w-2xl">
            {DEMO_MODE && (
              <motion.span
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-6 inline-flex items-center gap-2 rounded-full border border-neon/25 bg-neon/10 px-3.5 py-1.5 text-2xs font-semibold uppercase tracking-wider text-neon"
              >
                <Sparkles className="h-3 w-3" aria-hidden="true" /> Live demo — synthetic data
              </motion.span>
            )}

            <motion.h1
              initial={{ opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
              className="text-4xl font-bold leading-[1.06] sm:text-6xl lg:text-6xl xl:text-7xl"
            >
              Has your idea ever <span className="text-gradient">actually worked</span>?
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
              className="mt-6 max-w-xl text-base leading-relaxed text-text-mid sm:text-lg"
            >
              Describe an investing idea in plain English. Stratoscan turns it into explicit rules
              and replays them over ten years of daily prices &mdash; fees, slippage and the S&amp;P
              500 alongside. We don&rsquo;t tell you what to buy. We show you whether what you
              already believe has ever been true.
            </motion.p>

            {/* Search */}
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className="mt-10"
            >
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  submit()
                }}
                className="glass-strong flex items-center gap-2 rounded-2xl p-2 shadow-card"
                role="search"
              >
                <Search className="ml-3 h-5 w-5 shrink-0 text-neon" aria-hidden="true" />
                <div className="relative h-12 min-w-0 flex-1">
                  <input
                    ref={searchRef}
                    value={shown}
                    onChange={(e) => {
                      setEdited(true)
                      setQuery(e.target.value)
                    }}
                    onFocus={() => setFocused(true)}
                    onBlur={() => {
                      setFocused(false)
                      if (!query.trim()) setEdited(false)
                    }}
                    placeholder="Describe an idea to test…"
                    aria-label="Idea to test"
                    className="peer h-12 w-full bg-transparent text-sm text-transparent caret-neon placeholder:text-text-low focus:outline-none sm:text-base"
                    spellCheck={false}
                    autoComplete="off"
                  />
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-y-0 left-0 right-0 flex items-center overflow-hidden whitespace-nowrap text-sm text-text-hi sm:text-base"
                  >
                    {shown}
                    {!focused && !edited && shown && (
                      <span
                        className="ml-0.5 inline-block h-[1.15em] w-[2px] bg-neon"
                        style={{ animation: 'caret-blink 1.05s step-end infinite' }}
                      />
                    )}
                  </span>
                </div>
                <Button type="submit" size="lg" className="shrink-0">
                  Test it <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              </form>

              <div className="mt-5 flex flex-wrap items-center gap-2">
                <span className="text-2xs font-medium uppercase tracking-wider text-text-low">
                  Try
                </span>
                {HERO_TEMPLATES.map((t) => (
                  <button
                    key={t.id}
                    title={t.query}
                    onClick={() => {
                      // Identified by template id, never by text.
                      track('template_used', { template: t.id })
                      runSearch(t.query)
                    }}
                    className="glass-hover rounded-full px-3.5 py-1.5 text-xs text-text-mid"
                  >
                    {t.title}
                  </button>
                ))}
                <Link to="/strategies" className="text-xs font-medium text-neon hover:underline">
                  All templates →
                </Link>
              </div>
            </motion.div>

            {/* Count-up stats */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.8, delay: 0.45 }}
              className="mt-12 grid grid-cols-2 gap-x-8 gap-y-6 sm:grid-cols-4"
            >
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={10} suffix="y" />
                </p>
                <p className="mt-1 text-xs text-text-low">of daily prices per test</p>
              </div>
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={1000} />
                </p>
                <p className="mt-1 text-xs text-text-low">Monte Carlo replays per test</p>
              </div>
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={100} suffix="%" />
                </p>
                <p className="mt-1 text-xs text-text-low">of the numbers, free</p>
              </div>
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={7} />
                </p>
                <p className="mt-1 text-xs text-text-low">failure drills, published</p>
              </div>
            </motion.div>
            <Link
              to="/verification"
              className="mt-6 inline-flex items-center gap-1 text-xs font-medium text-neon hover:underline"
            >
              How we check the numbers <ArrowRight className="h-3 w-3" aria-hidden="true" />
            </Link>
          </div>

          {/* Right: terminal */}
          <motion.div
            initial={{ opacity: 0, y: 24, rotate: -1 }}
            animate={{ opacity: 1, y: 0, rotate: 0 }}
            transition={{ duration: 0.8, delay: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="relative"
          >
            <div
              className="pointer-events-none absolute -inset-6 rounded-3xl bg-gradient-to-br from-neon/10 via-transparent to-grape/10 blur-2xl"
              aria-hidden="true"
            />
            <TerminalHero />
          </motion.div>
        </div>

        {/* Kinetic marquee */}
        <div className="relative overflow-hidden border-y border-white/[0.06] bg-ink-deep/60 py-4 backdrop-blur-md">
          <div className="flex w-max animate-ticker gap-10" aria-hidden="true">
            {/* Exactly two copies so the -50% translate loops seamlessly */}
            {[...MARQUEE, ...MARQUEE].map((m, i) => (
              <span key={i} className="flex items-center gap-10 whitespace-nowrap">
                <span className="font-mono text-xs uppercase tracking-[0.2em] text-text-mid">
                  {m}
                </span>
                <span className="h-1 w-1 rounded-full bg-neon/50" />
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ── Manifesto ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-4xl px-5 py-24">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          className="relative text-center"
        >
          <div
            className="mx-auto mb-10 h-px w-24 bg-gradient-to-r from-transparent via-neon/70 to-transparent"
            aria-hidden="true"
          />
          <p className="mx-auto max-w-2xl text-lg leading-relaxed text-text-mid sm:text-xl">
            Most tools tell you what to buy.
          </p>
          <p className="mx-auto mt-5 max-w-3xl text-3xl font-bold leading-[1.15] sm:text-4xl lg:text-5xl">
            Stratoscan won&rsquo;t. It shows you whether what you{' '}
            <span className="text-gradient">already believe</span> has ever been true &mdash; and
            how it could be wrong.
          </p>
          <p className="mt-8 font-mono text-xs uppercase tracking-[0.3em] text-neon sm:text-sm">
            Evidence, not tips.
          </p>
        </motion.div>
      </section>

      <Pipeline />

      <BacktestShowcase />

      {/* ── Verification teaser (Day 14) ────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-5 py-16">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="glass-strong grid gap-8 rounded-3xl p-8 sm:p-10 lg:grid-cols-[1.2fr_1fr]"
        >
          <div>
            <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">
              Verification
            </p>
            <h2 className="mt-2 text-2xl font-bold sm:text-3xl">We publish the evidence.</h2>
            <p className="mt-3 text-sm leading-relaxed text-text-mid">
              Every backtester shows you numbers. Ours come with the proof that the arithmetic is
              right &mdash; frozen reference results, a loop that recomputes every figure, seven
              failure drills &mdash; and a plain list of what is not covered.
            </p>
            <Button
              size="lg"
              variant="outline"
              className="mt-6"
              onClick={() => navigate('/verification')}
            >
              How we check the numbers <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-6">
            <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
              A 2-for-1 split, bought and held
            </p>
            <dl className="mt-4 space-y-3 text-sm">
              <div className="flex items-baseline justify-between gap-4">
                <dt className="text-text-mid">A naive backtester reports</dt>
                <dd className="font-mono text-lg font-semibold tabular text-loss-soft">
                  &minus;50.1%
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-4">
                <dt className="text-text-mid">Stratoscan reports</dt>
                <dd className="font-mono text-lg font-semibold tabular text-text-hi">
                  &minus;0.3%
                </dd>
              </div>
            </dl>
            <p className="mt-4 text-xs leading-relaxed text-text-low">
              A split halves the price and doubles the shares &mdash; nobody loses a cent. The 0.3%
              is the real cost of the two trades.
            </p>
          </div>
        </motion.div>
      </section>

      {/* ── Pricing teaser ──────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-5 py-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="glass-hover flex flex-wrap items-center justify-between gap-6 rounded-3xl p-8"
        >
          <div>
            <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">Pricing</p>
            <h2 className="mt-2 text-2xl font-bold sm:text-3xl">
              Free to start. The numbers are never paywalled.
            </h2>
            <p className="mt-2 max-w-md text-sm text-text-mid">
              The full history, every robustness check and the code behind every result — on the
              free plan, forever. Pro adds volume, not accuracy.
            </p>
          </div>
          <Button size="lg" variant="grape" onClick={() => navigate('/pricing')}>
            See pricing <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </motion.div>
      </section>

      {/* ── Final CTA ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-5 py-24">
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="glass-strong relative overflow-hidden rounded-3xl p-10 text-center shadow-card sm:p-16"
        >
          <div
            className="pointer-events-none absolute -top-24 left-1/2 h-64 w-[520px] -translate-x-1/2 rounded-full bg-grape/20 blur-[90px]"
            aria-hidden="true"
          />
          <h2 className="relative text-3xl font-bold sm:text-5xl">
            Test your next idea before you trust it
          </h2>
          <p className="relative mx-auto mt-4 max-w-md text-text-mid">
            One sentence in, ten years of evidence out. Three free tests without an account; no card
            on the free plan.
          </p>
          <div className="relative mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" onClick={() => navigate(user ? '/app/backtest' : '/try')}>
              Try it free — no signup <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button size="lg" variant="outline" onClick={() => navigate('/login')}>
              Log in
            </Button>
          </div>
        </motion.div>
      </section>

      <Footer />
    </div>
  )
}
