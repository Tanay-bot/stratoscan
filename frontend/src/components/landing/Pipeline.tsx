import { motion } from 'framer-motion'
import { ArrowRight, BarChart3, BrainCircuit, Search } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

const NODES = [
  {
    icon: BrainCircuit,
    step: '01 · Describe',
    title: 'Type your idea',
    body: '“Find undervalued small-cap tech stocks” — no filters, no query language.',
  },
  {
    icon: Search,
    step: '02 · Screen',
    title: 'Ranked candidates',
    body: 'The interpreter extracts criteria and ranks about 5,000 US stocks in about a second.',
  },
  {
    icon: BarChart3,
    step: '03 · Backtest',
    title: 'Validated thesis',
    body: '10 years of simulated trading with stops, sizing and a SPY benchmark.',
  },
]

export function Pipeline() {
  const navigate = useNavigate()
  return (
    <section className="mx-auto max-w-7xl px-5 py-20">
      <div className="mb-14 text-center">
        <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-neon">The loop</p>
        <h2 className="mt-3 text-3xl font-bold sm:text-4xl">From thought to thesis</h2>
      </div>

      <div className="relative">
        {/* Animated connector */}
        <div
          className="pointer-events-none absolute left-0 right-0 top-10 hidden h-px lg:block"
          aria-hidden="true"
        >
          <div className="h-px w-full bg-gradient-to-r from-neon/40 via-grape/40 to-neon/40" />
          <motion.div
            className="absolute -top-[3px] h-[7px] w-[7px] rounded-full bg-neon shadow-glow-sm"
            animate={{ left: ['0%', '100%', '0%'] }}
            transition={{ duration: 9, repeat: Infinity, ease: 'easeInOut' }}
          />
        </div>

        <div className="grid gap-8 lg:grid-cols-3">
          {NODES.map(({ icon: Icon, step, title, body }, i) => (
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-80px' }}
              transition={{ duration: 0.55, delay: i * 0.14, ease: [0.16, 1, 0.3, 1] }}
              className="glass-hover relative rounded-2xl p-6"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-neon/20 to-grape/25 shadow-[inset_0_0_0_1px_rgba(0,212,255,0.2)]">
                <Icon className="h-5 w-5 text-neon" aria-hidden="true" />
              </div>
              <p className="mt-5 font-mono text-2xs font-semibold uppercase tracking-wider text-neon">
                {step}
              </p>
              <h3 className="mt-2 text-lg font-semibold">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-text-mid">{body}</p>
            </motion.div>
          ))}
        </div>
      </div>

      <div className="mt-12 flex flex-col items-center justify-center gap-4 sm:flex-row">
        <button
          onClick={() => navigate('/try')}
          className="group flex items-center gap-2 text-sm font-medium text-neon transition-colors hover:text-white"
        >
          Run the loop yourself
          <ArrowRight
            className="h-4 w-4 transition-transform group-hover:translate-x-1"
            aria-hidden="true"
          />
        </button>
        <span className="hidden h-4 w-px bg-white/10 sm:block" aria-hidden="true" />
        <button
          onClick={() => navigate('/how-it-works')}
          className="group flex items-center gap-2 text-sm font-medium text-text-mid transition-colors hover:text-text-hi"
        >
          How it works in depth
          <ArrowRight
            className="h-4 w-4 transition-transform group-hover:translate-x-1"
            aria-hidden="true"
          />
        </button>
      </div>
    </section>
  )
}
