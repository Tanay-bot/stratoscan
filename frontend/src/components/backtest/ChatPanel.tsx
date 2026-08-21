import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Send, SlidersHorizontal, Sparkles, WandSparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useRateLimitCountdown } from '@/store/rateLimit'

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  meta?: string[]
}

const SUGGESTIONS = [
  'buy oversold tech stocks with rising momentum',
  'high-dividend energy companies, SMA 50 above SMA 200',
  'momentum breakout, RSI below 30, stop loss 8%',
]

export function ChatPanel({
  messages,
  loading,
  onSend,
  tuneOpen,
  onToggleTune,
  tune,
  maxLength = 4000,
}: {
  messages: ChatMessage[]
  loading: boolean
  onSend: (text: string) => void
  tuneOpen: boolean
  onToggleTune: () => void
  tune: React.ReactNode
  /** Backend query max_length minus any overhead the parent adds. */
  maxLength?: number
}) {
  const [draft, setDraft] = useState('')
  const { pending, waitSeconds } = useRateLimitCountdown()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, loading])

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault()
    const q = draft.trim()
    if (!q || loading || pending) return
    setDraft('')
    onSend(q)
  }

  return (
    <section
      aria-label="Strategy chat"
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-white/[0.07] bg-ink-panel/70"
    >
      {/* Header */}
      <header className="flex shrink-0 items-center gap-2 border-b border-white/[0.06] px-4 py-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-neon/25 to-grape/30">
          <WandSparkles className="h-3.5 w-3.5 text-neon" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-tight text-text-hi">Strategy chat</p>
          <p className="truncate text-2xs text-text-low">Natural language → backtest</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onToggleTune}
          aria-expanded={tuneOpen}
          aria-controls="tune-drawer"
          aria-label={tuneOpen ? 'Hide tuning parameters' : 'Show tuning parameters'}
          className={cn(tuneOpen && 'border-neon/40 text-neon')}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
          {tuneOpen ? 'Done' : 'Tune'}
        </Button>
      </header>

      {/* Messages */}
      <div
        ref={scrollRef}
        aria-live="polite"
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4"
      >
        {messages.length === 0 && !loading && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-3"
          >
            <div className="glass rounded-2xl rounded-bl-md p-4">
              <p className="flex items-center gap-2 text-sm font-semibold text-text-hi">
                <Sparkles className="h-4 w-4 text-neon" aria-hidden="true" />
                Describe a strategy in plain English
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-text-mid">
                I'll interpret it, generate the scan code, and replay 10 years of simulated trading
                against SPY — right here.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => onSend(s)}
                  disabled={pending}
                  className="glass-hover rounded-full border border-white/10 px-3 py-1.5 text-xs text-text-mid transition-colors hover:border-neon/40 hover:text-neon disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {s}
                </button>
              ))}
            </div>
          </motion.div>
        )}

        <AnimatePresence initial={false}>
          {messages.map((m) => (
            <motion.div
              key={m.id}
              initial={{ opacity: 0, y: 10, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              data-chat-msg
              data-role={m.role}
              className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}
            >
              <div
                className={cn(
                  'max-w-[85%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed',
                  m.role === 'user'
                    ? 'rounded-br-md border border-neon/25 bg-neon/[0.12] text-text-hi'
                    : 'glass rounded-bl-md text-text-mid',
                )}
              >
                <p className="whitespace-pre-wrap">{m.text}</p>
                {m.meta && m.meta.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {m.meta.map((line) => (
                      <li key={line} className="flex items-start gap-1.5 text-xs text-text-low">
                        <span
                          className="mt-[5px] h-1 w-1 shrink-0 rounded-full bg-neon"
                          aria-hidden="true"
                        />
                        <span className="font-mono text-[11px]">{line}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex justify-start"
          >
            <div className="glass flex items-center gap-1.5 rounded-2xl rounded-bl-md px-4 py-3">
              {[0, 1, 2].map((i) => (
                <motion.span
                  key={i}
                  className="h-1.5 w-1.5 rounded-full bg-neon"
                  animate={{ opacity: [0.2, 1, 0.2], y: [0, -3, 0] }}
                  transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }}
                />
              ))}
              <span className="ml-1.5 text-xs text-text-low">Interpreting…</span>
            </div>
          </motion.div>
        )}
      </div>

      {/* Tune drawer */}
      <AnimatePresence>
        {tuneOpen && (
          <motion.div
            id="tune-drawer"
            role="region"
            aria-label="Strategy parameters"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="shrink-0 overflow-hidden border-t border-white/[0.06]"
          >
            <div className="max-h-64 overflow-y-auto px-4 py-3">{tune}</div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Composer */}
      <form onSubmit={submit} className="shrink-0 border-t border-white/[0.06] p-3">
        <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-1.5 transition-colors focus-within:border-neon/50 focus-within:ring-2 focus-within:ring-neon/20">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={maxLength}
            placeholder="e.g. buy oversold tech stocks…"
            aria-label="Strategy message"
            spellCheck={false}
            className="h-8 min-w-0 flex-1 bg-transparent text-sm text-text-hi placeholder:text-text-low focus:outline-none"
          />
          <button
            type="submit"
            disabled={!draft.trim() || loading || pending}
            aria-label="Send strategy"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-neon text-ink transition-all hover:shadow-glow-sm disabled:cursor-not-allowed disabled:opacity-35"
          >
            <Send className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        {draft.length > maxLength * 0.8 && (
          <p
            className={cn(
              'mt-1 px-1 font-mono text-2xs tabular',
              draft.length >= maxLength ? 'text-loss-soft' : 'text-amber-300',
            )}
          >
            {draft.length} / {maxLength}
          </p>
        )}
        {pending && (
          <p
            role="status"
            className="mt-1.5 flex items-center gap-1.5 px-1 text-2xs font-medium text-amber-300"
          >
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
            Too many requests — retrying automatically in {waitSeconds}s
          </p>
        )}
        <p className="mt-1.5 px-1 text-2xs text-text-low">
          <span className="font-semibold text-neon">Run backtest</span> or press{' '}
          <kbd className="rounded border border-white/15 px-1 font-mono text-[10px]">Enter</kbd> to
          scan now.
        </p>
      </form>
    </section>
  )
}
