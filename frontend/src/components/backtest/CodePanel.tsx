import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, CircleDashed, FileCode2, TerminalSquare, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface CodeParams {
  capital: number
  startDate: string
  endDate: string
  rsiBuy: number
  rsiSell: number
  smaFast: number
  smaSlow: number
  stopLoss: number
  takeProfit: number
  allowShort: boolean
  topN: number
}

const DEFAULT_PARAMS: CodeParams = {
  capital: 100000,
  startDate: '',
  endDate: '',
  rsiBuy: 30,
  rsiSell: 70,
  smaFast: 20,
  smaSlow: 50,
  stopLoss: 8,
  takeProfit: 20,
  allowShort: false,
  topN: 5,
}

/** Keep user text from breaking the fake script's string literals. */
const safe = (s: string) =>
  s
    .replace(/["\\\r\n]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120)

/** Build a plausible "generated strategy" script from the real query + params. */
function buildScript(query: string, p: CodeParams): string[] {
  const q = safe(query)
  const start = p.startDate || '2016-01-01'
  const end = p.endDate || '2026-01-01'
  return [
    '# stratoscan · generated strategy',
    `# query: "${q}"`,
    '',
    'from stratoscan import Screen, Signal, Backtest',
    '',
    'screen = Screen("US_EQUITIES_10Y")',
    `screen.thesis("${q}")`,
    '',
    '# entry signals',
    `screen.add(Signal.RSI(below=${p.rsiBuy}))`,
    `screen.add(Signal.RSI(above=${p.rsiSell}))`,
    `screen.add(Signal.SMA(cross=${p.smaFast}/${p.smaSlow}))`,
    '',
    'engine = Backtest(',
    `    capital=${p.capital},`,
    `    start="${start}",`,
    `    end="${end}",`,
    `    stop_loss=${p.stopLoss},`,
    `    take_profit=${p.takeProfit},`,
    `    allow_short=${String(p.allowShort).toLowerCase()},`,
    ')',
    '',
    'result = engine.run(screen, top_n=' + String(p.topN) + ')',
    'print(result.summary())',
  ]
}

// Simple tokenizer: comments, strings, numbers, keywords, idents, punctuation
const KW = new Set([
  'from',
  'import',
  'def',
  'return',
  'if',
  'else',
  'for',
  'in',
  'True',
  'False',
  'None',
  'print',
])

function renderLine(line: string, idx: number): React.ReactNode {
  if (line === '') return <div key={idx} className="h-4" />
  const parts = line.split(/(#[^\n]*|"[^"]*"|\b\d+(?:\.\d+)?\b|'[^']*')/)
  return (
    <div key={idx} className="flex">
      <span className="w-8 shrink-0 select-none pr-3 text-right text-[10px] leading-[1.6] text-white/15">
        {idx + 1}
      </span>
      <code className="whitespace-pre">
        {parts.map((tok, i) => {
          if (tok.startsWith('#'))
            return (
              <span key={i} className="text-text-low/70 italic">
                {tok}
              </span>
            )
          if (
            (tok.startsWith('"') && tok.endsWith('"')) ||
            (tok.startsWith("'") && tok.endsWith("'"))
          )
            return (
              <span key={i} className="text-gain-soft">
                {tok}
              </span>
            )
          if (/^\d/.test(tok))
            return (
              <span key={i} className="text-neon">
                {tok}
              </span>
            )
          if (KW.has(tok))
            return (
              <span key={i} className="text-grape-soft">
                {tok}
              </span>
            )
          return <span key={i}>{tok}</span>
        })}
      </code>
    </div>
  )
}

export function CodePanel({
  status,
  query,
  params = DEFAULT_PARAMS,
  symbols,
}: {
  /** idle = nothing run yet; running = streaming; done = finished; error = failed */
  status: 'idle' | 'running' | 'done' | 'error'
  query: string
  params?: CodeParams
  symbols?: number
}) {
  const script = useMemo(
    () => buildScript(query.trim() || 'your strategy', params),
    [query, params],
  )
  const [visible, setVisible] = useState(0)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Stream lines until the full script is revealed; a status flip mid-stream
  // (running → done) never jumps the reveal — it lets the stream finish, so the
  // generated-code visual always plays out while the real request is in flight.
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (status === 'idle') {
      setVisible(0)
      return
    }
    intervalRef.current = setInterval(() => {
      setVisible((v) => {
        const next = Math.min(v + 1, script.length)
        if (next >= script.length && intervalRef.current) {
          clearInterval(intervalRef.current)
        }
        return next
      })
    }, 60)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [status, script])

  const streaming = visible < script.length
  const meta =
    status === 'done' && !streaming
      ? {
          label: `Backtest complete — ${symbols ?? '—'} symbols tested`,
          dot: 'bg-gain',
          Icon: CheckCircle2,
        }
      : status === 'done'
        ? { label: 'Finalizing generated script…', dot: 'bg-neon animate-pulse', Icon: FileCode2 }
        : status === 'error'
          ? { label: 'Backtest failed', dot: 'bg-loss', Icon: XCircle }
          : status === 'running'
            ? { label: 'Generating scan code…', dot: 'bg-neon animate-pulse', Icon: FileCode2 }
            : { label: 'Awaiting strategy…', dot: 'bg-text-low/40', Icon: CircleDashed }
  const { Icon, dot, label } = meta

  return (
    <section
      aria-label="Generated strategy code"
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a0d16]/90"
    >
      {/* Terminal chrome */}
      <header className="flex shrink-0 items-center gap-3 border-b border-white/[0.06] px-4 py-2.5">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        </div>
        <span className="flex items-center gap-1.5 font-mono text-2xs text-text-low">
          <TerminalSquare className="h-3 w-3 text-grape-soft" aria-hidden="true" />
          stratoscan_generate.py
        </span>
        <span className="ml-auto flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-2xs text-text-mid">
          <span className={cn('h-1.5 w-1.5 rounded-full', dot)} aria-hidden="true" />
          <span data-code-status>{status}</span>
        </span>
      </header>

      {/* Code body */}
      <div className="min-h-0 flex-1 overflow-y-auto p-4 font-mono text-[12px] leading-[1.6] text-text-mid">
        {status === 'idle' ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex h-full flex-col items-center justify-center gap-2 text-center"
          >
            <FileCode2 className="h-8 w-8 text-white/10" aria-hidden="true" />
            <p className="text-xs text-text-low">Generated scan code streams here</p>
            <p className="max-w-xs text-[11px] text-text-low/60">
              Your natural-language strategy is compiled into a script — capital, signals, risk and
              date range included — while the real backtest runs.
            </p>
          </motion.div>
        ) : (
          <div className="space-y-0">
            {script.slice(0, visible).map((l, i) => renderLine(l, i))}
            {streaming && (
              <div className="flex">
                <span className="w-8 shrink-0" />
                <span className="inline-block h-4 w-2 animate-pulse bg-neon" aria-hidden="true" />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Status bar */}
      <footer className="flex shrink-0 items-center gap-2 border-t border-white/[0.06] bg-white/[0.02] px-4 py-2">
        <Icon
          className={cn(
            'h-3.5 w-3.5',
            status === 'done'
              ? 'text-gain-soft'
              : status === 'error'
                ? 'text-loss-soft'
                : status === 'running'
                  ? 'text-neon'
                  : 'text-text-low',
          )}
          aria-hidden="true"
        />
        <span className="text-[11px] text-text-mid">{label}</span>
        {status === 'done' && (
          <span className="ml-auto font-mono text-[10px] text-text-low">
            ≈ {script.length} lines · 10y daily bars
          </span>
        )}
      </footer>
    </section>
  )
}
