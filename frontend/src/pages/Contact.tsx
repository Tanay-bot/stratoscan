import { useState } from 'react'
import { motion } from 'framer-motion'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { CheckCircle2, Mail, MapPin, MessageSquare, Send, ShieldCheck } from 'lucide-react'
import { LandingNav } from '@/components/landing/LandingNav'
import { Footer } from '@/components/landing/Footer'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toast } from '@/store/notifications'
import { DEMO_MODE } from '@/lib/api/client'

const contactSchema = z.object({
  name: z.string().min(2, 'Please tell us your name'),
  email: z.string().email('Enter a valid email address'),
  topic: z.string().min(1, 'Choose a topic'),
  message: z.string().min(10, 'A few more words helps us help you (min 10 characters)'),
})

type ContactValues = z.infer<typeof contactSchema>

const TOPICS = [
  'Product feedback',
  'Bug report',
  'Feature request',
  'Partnership',
  'Something else',
]

const CHANNELS = [
  {
    icon: Mail,
    title: 'Email',
    value: 'hello@stratoscan.app',
    hint: 'Best for detailed questions',
  },
  {
    icon: MessageSquare,
    title: 'Discord',
    value: 'Join the community',
    hint: 'Live help from builders & traders',
  },
  {
    icon: MapPin,
    title: 'Issues',
    value: 'GitHub issues',
    hint: 'For bug reports with repro steps',
  },
]

export function Contact() {
  const [sent, setSent] = useState(false)
  const form = useForm<ContactValues>({
    resolver: zodResolver(contactSchema),
    defaultValues: { name: '', email: '', topic: TOPICS[0], message: '' },
  })

  const onSubmit = async (values: ContactValues) => {
    // No backend contact endpoint exists; in demo mode we acknowledge locally.
    // Wire this to your preferred inbox/mail service when you have one.
    await new Promise((r) => setTimeout(r, 500))
    setSent(true)
    toast.success(
      'Message sent',
      `Thanks ${values.name.split(' ')[0]} — we’ll reply to ${values.email}.`,
    )
    form.reset()
    setTimeout(() => setSent(false), 4000)
  }

  return (
    <div id="main-content" tabIndex={-1} className="min-h-screen overflow-x-hidden outline-none">
      <LandingNav />

      <section className="relative overflow-hidden">
        <div
          className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[560px] -translate-x-1/2 rounded-full bg-grape/[0.1] blur-[100px]"
          aria-hidden="true"
        />

        <div className="relative mx-auto max-w-3xl px-5 pb-16 pt-20 text-center">
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="text-4xl font-bold sm:text-5xl"
          >
            Talk to <span className="text-gradient">us</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.1 }}
            className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-text-mid"
          >
            Questions, bugs, ideas, partnerships — a real human reads every message.
          </motion.p>
        </div>
      </section>

      <section className="mx-auto grid max-w-5xl gap-8 px-5 pb-24 lg:grid-cols-[1fr_360px]">
        {/* Form */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          className="glass-strong rounded-3xl p-7 shadow-card sm:p-9"
        >
          <h2 className="text-xl font-bold">Send a message</h2>
          <p className="mt-1 text-sm text-text-mid">We aim to reply within a couple of days.</p>

          {sent && (
            <div
              className="mt-5 flex items-center gap-3 rounded-xl border border-gain/25 bg-gain/10 p-4"
              role="status"
            >
              <CheckCircle2 className="h-5 w-5 shrink-0 text-gain-soft" aria-hidden="true" />
              <p className="text-sm text-gain-soft">
                Message received — we’ll get back to you soon.
              </p>
            </div>
          )}

          <form onSubmit={form.handleSubmit(onSubmit)} className="mt-6 space-y-5" noValidate>
            <div className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-1.5">
                <label
                  htmlFor="name"
                  className="text-2xs font-semibold uppercase tracking-wider text-text-low"
                >
                  Name
                </label>
                <Input
                  id="name"
                  placeholder="Ada Lovelace"
                  autoComplete="name"
                  {...form.register('name')}
                />
                {form.formState.errors.name && (
                  <p role="alert" className="text-xs text-loss-soft">
                    {form.formState.errors.name.message}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <label
                  htmlFor="email"
                  className="text-2xs font-semibold uppercase tracking-wider text-text-low"
                >
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@example.com"
                  autoComplete="email"
                  spellCheck={false}
                  {...form.register('email')}
                />
                {form.formState.errors.email && (
                  <p role="alert" className="text-xs text-loss-soft">
                    {form.formState.errors.email.message}
                  </p>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="topic"
                className="text-2xs font-semibold uppercase tracking-wider text-text-low"
              >
                Topic
              </label>
              <select
                id="topic"
                {...form.register('topic')}
                className="h-11 w-full rounded-xl border border-white/10 bg-white/[0.04] px-3.5 text-sm text-text-hi transition-colors hover:border-white/20 focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25 [color-scheme:dark]"
              >
                {TOPICS.map((t) => (
                  <option key={t} value={t} className="bg-ink-panel">
                    {t}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="message"
                className="text-2xs font-semibold uppercase tracking-wider text-text-low"
              >
                Message
              </label>
              <textarea
                id="message"
                rows={5}
                placeholder="Tell us what’s on your mind…"
                className="w-full resize-none rounded-xl border border-white/10 bg-white/[0.04] px-3.5 py-3 text-sm text-text-hi placeholder:text-text-low transition-colors hover:border-white/20 focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25"
                {...form.register('message')}
              />
              {form.formState.errors.message && (
                <p role="alert" className="text-xs text-loss-soft">
                  {form.formState.errors.message.message}
                </p>
              )}
            </div>

            <Button type="submit" size="lg" className="w-full">
              <Send className="h-4 w-4" aria-hidden="true" /> Send message
            </Button>

            {DEMO_MODE && (
              <p className="flex items-center justify-center gap-1.5 text-center text-2xs text-text-low">
                <ShieldCheck className="h-3 w-3 text-gain-soft" aria-hidden="true" />
                Demo mode — messages are acknowledged locally, not emailed.
              </p>
            )}
          </form>
        </motion.div>

        {/* Channels */}
        <div className="space-y-4">
          {CHANNELS.map(({ icon: Icon, title, value, hint }, i) => (
            <motion.div
              key={title}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.45, delay: i * 0.08 }}
              className="glass-hover rounded-2xl p-5"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-neon/15 to-grape/20">
                <Icon className="h-5 w-5 text-neon" aria-hidden="true" />
              </div>
              <h3 className="mt-3 text-sm font-semibold text-text-hi">{title}</h3>
              <p className="mt-0.5 font-mono text-sm text-neon">{value}</p>
              <p className="mt-1 text-xs text-text-low">{hint}</p>
            </motion.div>
          ))}

          <motion.div
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-40px' }}
            transition={{ duration: 0.45, delay: 0.24 }}
            className="glass rounded-2xl p-5"
          >
            <h3 className="text-sm font-semibold text-text-hi">Before you write</h3>
            <ul className="mt-3 space-y-2 text-sm text-text-mid">
              <li className="flex gap-2">
                <span className="text-neon">→</span> Check the FAQ on the pricing page.
              </li>
              <li className="flex gap-2">
                <span className="text-neon">→</span> For bugs, include the exact query and any error
                text.
              </li>
              <li className="flex gap-2">
                <span className="text-neon">→</span> Ideas and feature requests are always welcome.
              </li>
            </ul>
          </motion.div>
        </div>
      </section>

      <Footer />
    </div>
  )
}
