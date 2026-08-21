import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { FileText } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { cn } from '@/lib/utils'

export interface LegalSection {
  id: string
  title: string
  body: ReactNode
}

/** Shared shell for Terms / Privacy — nav, hero, table of contents, sections, footer. */
export function LegalPage({
  eyebrow,
  title,
  intro,
  lastUpdated,
  sections,
}: {
  eyebrow: string
  title: string
  intro: string
  lastUpdated: string
  sections: LegalSection[]
}) {
  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="grid-backdrop absolute inset-0" aria-hidden="true" />
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-64 w-[480px] -translate-x-1/2 rounded-full bg-neon/[0.07] blur-[100px]"
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-3xl px-5 pb-10 pt-16 text-center sm:pt-20">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-neon/15 to-grape/20"
          >
            <FileText className="h-5 w-5 text-neon" aria-hidden="true" />
          </motion.div>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.05 }}
            className="text-2xs font-semibold uppercase tracking-[0.25em] text-neon"
          >
            {eyebrow}
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="mt-3 text-4xl font-bold sm:text-5xl"
          >
            {title}
          </motion.h1>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.15 }}
            className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-text-mid"
          >
            {intro}
          </motion.p>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className="mt-4 font-mono text-2xs uppercase tracking-wider text-text-low"
          >
            Last updated: {lastUpdated}
          </motion.p>
        </div>
      </section>

      {/* Body */}
      <section className="mx-auto max-w-3xl px-5 pb-24 pt-8">
        {/* Table of contents */}
        <nav aria-label="On this page" className="glass mb-10 rounded-2xl p-6">
          <p className="text-2xs font-semibold uppercase tracking-[0.2em] text-text-low">
            On this page
          </p>
          <ul className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {sections.map((s, i) => (
              <li key={s.id}>
                <a
                  href={`#${s.id}`}
                  className="group flex items-baseline gap-2 text-sm text-text-mid transition-colors hover:text-neon"
                >
                  <span className="font-mono text-2xs tabular text-text-low group-hover:text-neon">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  {s.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        {/* Sections */}
        <div className="space-y-10">
          {sections.map((s, i) => (
            <motion.section
              key={s.id}
              id={s.id}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.4, delay: Math.min(i * 0.03, 0.2) }}
              className="scroll-mt-24"
            >
              <h2 className="flex items-center gap-3 text-xl font-semibold sm:text-2xl">
                <span aria-hidden="true" className="font-mono text-xs tabular text-neon">
                  {String(i + 1).padStart(2, '0')}
                </span>
                {s.title}
              </h2>
              <div
                className={cn(
                  'mt-4 space-y-4 text-[15px] leading-relaxed text-text-mid',
                  // list styling inside legal body blocks
                  '[&_ul]:mt-3 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5 [&_ol]:mt-3 [&_ol]:list-decimal [&_ol]:space-y-1.5 [&_ol]:pl-5',
                )}
              >
                {s.body}
              </div>
            </motion.section>
          ))}
        </div>

        {/* Cross-link */}
        <div className="mt-14 flex flex-col items-center justify-between gap-4 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-6 sm:flex-row">
          <p className="text-sm text-text-mid">
            Questions about this document? Reach out any time.
          </p>
          <div className="flex items-center gap-5 text-sm">
            <Link to="/contact" className="text-neon transition-opacity hover:opacity-80">
              Contact us
            </Link>
            <Link to="/privacy" className="text-text-mid transition-colors hover:text-text-hi">
              Privacy policy
            </Link>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  )
}
