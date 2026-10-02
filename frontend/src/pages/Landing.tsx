import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
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
import { useToasts } from '@/store/notifications'
import { useRegisterSearchInput } from '@/hooks/useKeyboardShortcuts'

const EXAMPLES = [
  'find undervalued small-cap tech stocks',
  'high-dividend energy companies',
  'momentum breakout stocks',
]

const MARQUEE = [
  'natural language',
  '10 years of history',
  'monte carlo',
  'walk-forward validation',
  'sentiment pipeline',
  'spy benchmark',
  'fully free',
  'no spreadsheet required',
]

const SEARCH_PROMPTS = [
  'find undervalued small-cap tech stocks',
  'high-dividend energy companies',
  'momentum breakout stocks',
  'oversold healthcare with low beta',
]

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
      toasts.push('info', 'Type an idea to screen', 'Try one of the example queries below.')
      return
    }
    navigate(
      user
        ? `/app/screener?q=${encodeURIComponent(trimmed)}`
        : `/login?next=${encodeURIComponent(`/app/screener?q=${encodeURIComponent(trimmed)}`)}`,
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
              Bridge from <span className="text-gradient">idea</span> to candidate list.
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
              className="mt-6 max-w-xl text-base leading-relaxed text-text-mid sm:text-lg"
            >
              Stratoscan turns plain-English investing ideas into ranked, backtested candidate lists
              — with the depth of a terminal and none of the learning curve. Free, forever.
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
                    placeholder="Describe the stocks you want to find…"
                    aria-label="Screen query"
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
                  Screen <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              </form>

              <div className="mt-5 flex flex-wrap items-center gap-2">
                <span className="text-2xs font-medium uppercase tracking-wider text-text-low">
                  Try
                </span>
                {EXAMPLES.map((ex, i) => (
                  <button
                    key={ex}
                    onClick={() => {
                      // Identified by position, never by text: the query the
                      // user sends is theirs, the chip is ours. Indexes here
                      // are stable because the list is a module constant.
                      track('template_used', { template: `landing-example-${i}` })
                      runSearch(ex)
                    }}
                    className="glass-hover rounded-full px-3.5 py-1.5 text-xs text-text-mid"
                  >
                    {ex}
                  </button>
                ))}
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
                  <CountUp target={3} suffix="s" />
                </p>
                <p className="mt-1 text-xs text-text-low">from idea to candidates</p>
              </div>
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={10} suffix="y" />
                </p>
                <p className="mt-1 text-xs text-text-low">of backtest history</p>
              </div>
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={100} suffix="%" />
                </p>
                <p className="mt-1 text-xs text-text-low">free — no paywall</p>
              </div>
              <div>
                <p className="font-mono text-3xl font-bold tabular text-text-hi">
                  <CountUp target={24} suffix="/7" />
                </p>
                <p className="mt-1 text-xs text-text-low">market coverage</p>
              </div>
            </motion.div>
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
            Most screeners flood you with complex data and rigid filters.
          </p>
          <p className="mx-auto mt-5 max-w-3xl text-3xl font-bold leading-[1.15] sm:text-4xl lg:text-5xl">
            Stratoscan lets you type what you're looking for in{' '}
            <span className="text-gradient">plain English</span> — and surfaces high-potential
            stocks instantly.
          </p>
          <p className="mt-8 whitespace-nowrap font-mono text-xs uppercase tracking-[0.3em] text-neon sm:text-sm">
            No spreadsheet required.
          </p>
        </motion.div>
      </section>

      <Pipeline />

      <BacktestShowcase />

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
            <h2 className="mt-2 text-2xl font-bold sm:text-3xl">One plan. Zero dollars.</h2>
            <p className="mt-2 max-w-md text-sm text-text-mid">
              No tiers, no credits, no paywall. Every feature, for everyone, forever.
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
            Your next idea is one sentence away
          </h2>
          <p className="relative mx-auto mt-4 max-w-md text-text-mid">
            Describe it once, screen it instantly, backtest it for free. No paywall, no credits, no
            catch.
          </p>
          <div className="relative mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" onClick={() => navigate('/signup')}>
              Start screening free <ArrowRight className="h-4 w-4" aria-hidden="true" />
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
