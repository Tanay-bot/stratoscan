import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ALL_STOCKS, priceHistory } from '@/lib/api/demo'
import { DEMO_MODE } from '@/lib/api/client'
import { listStocks } from '@/lib/api/services'
import { fmtCurrency } from '@/lib/utils'
import { cn } from '@/lib/utils'

const REDUCED =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const QUERIES = [
  'find undervalued small-cap tech stocks',
  'high-dividend energy companies',
  'momentum breakout stocks',
  'oversold healthcare with low beta',
]

interface Candle {
  o: number
  h: number
  l: number
  c: number
  up: boolean
}

function makeCandles(symbol: string, n = 42): Candle[] {
  const hist = priceHistory(symbol, n * 3)
  const candles: Candle[] = []
  for (let i = 0; i < n; i++) {
    const o = hist[i * 3].value
    const c = hist[i * 3 + 2].value
    const h = Math.max(o, c) * 1.006
    const l = Math.min(o, c) * 0.994
    candles.push({ o, h, l, c, up: c >= o })
  }
  return candles
}

function useTypewriter(phrases: string[], typeMs = 38, holdMs = 2400) {
  const [text, setText] = useState('')
  const [phraseIdx, setPhraseIdx] = useState(0)

  useEffect(() => {
    if (REDUCED) {
      setText(phrases[0])
      return
    }
    const phrase = phrases[phraseIdx % phrases.length]
    let i = 0
    let direction: 1 | -1 = 1
    let timer: ReturnType<typeof setTimeout>

    const step = () => {
      if (direction === 1) {
        i++
        setText(phrase.slice(0, i))
        if (i === phrase.length) {
          direction = -1
          timer = setTimeout(step, holdMs)
          return
        }
        timer = setTimeout(step, typeMs)
      } else {
        i--
        setText(phrase.slice(0, i))
        if (i === 0) {
          direction = 1
          setPhraseIdx((v) => v + 1)
          timer = setTimeout(step, 500)
          return
        }
        timer = setTimeout(step, typeMs / 2.5)
      }
    }
    timer = setTimeout(step, 700)
    return () => clearTimeout(timer)
  }, [phraseIdx, phrases, typeMs, holdMs])

  return text
}

/** Live ticking prices for the terminal footer.
 *
 * Demo mode: synthetic drift every 1.4s. Production: real prices from
 * /stocks on mount, with a gentle client-side drift to simulate live
 * ticking (the API prices update every 10-minute refresh, not tick-by-tick).
 */
function useLiveQuotes(symbols: string[]) {
  const [quotes, setQuotes] = useState(() => {
    if (DEMO_MODE) {
      return symbols.map((s) => {
        const stock = ALL_STOCKS.find((x) => x.symbol === s)
        return { sym: s, price: stock?.current_price ?? 100, chg: stock?.price_change_pct ?? 0 }
      })
    }
    // Production: start empty, fill from API
    return symbols.map((s) => ({ sym: s, price: null as number | null, chg: 0 }))
  })

  // Production: fetch real prices from the backend
  useEffect(() => {
    if (DEMO_MODE) return
    let cancelled = false
    listStocks(50, 0)
      .then((res) => {
        if (cancelled) return
        const bySymbol = new Map(res.results.map((s) => [s.symbol, s]))
        setQuotes((prev) =>
          prev.map((q) => {
            const real = bySymbol.get(q.sym)
            if (!real) return q
            return { sym: q.sym, price: real.current_price ?? q.price, chg: real.price_change_pct ?? 0 }
          }),
        )
      })
      .catch(() => { /* API unreachable — quotes stay null, handled below */ })
    return () => { cancelled = true }
  }, [])

  // Gentle client-side drift so prices feel alive between refreshes
  useEffect(() => {
    if (REDUCED) return
    const id = setInterval(() => {
      setQuotes((prev) =>
        prev.map((q) => {
          if (q.price === null) return q
          const drift = (Math.random() - 0.5) * 0.006
          const price = q.price * (1 + drift)
          return { ...q, price, chg: q.chg + drift * 100 }
        }),
      )
    }, 1400)
    return () => clearInterval(id)
  }, [])

  return quotes
}

export function TerminalHero() {
  const typed = useTypewriter(QUERIES)
  const candles = useMemo(() => (DEMO_MODE ? makeCandles('NVDA') : []), [])
  const liveQuotes = useLiveQuotes(['NVDA', 'AAPL', 'META', 'LLY'])

  const max = Math.max(...candles.map((c) => c.h), 0)
  const min = Math.min(...candles.map((c) => c.l), 0)
  const span = max - min || 1
  const w = 640
  const h = 200
  const step = w / Math.max(1, candles.length)

  const y = (v: number) => h - 14 - ((v - min) / span) * (h - 28)

  return (
    <div className="glass-strong relative overflow-hidden rounded-2xl shadow-card">
      {/* Window chrome */}
      <div className="flex items-center gap-2 border-b border-white/[0.08] px-4 py-3">
        <span className="h-2.5 w-2.5 rounded-full bg-loss/80" aria-hidden="true" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" aria-hidden="true" />
        <span className="h-2.5 w-2.5 rounded-full bg-gain/80" aria-hidden="true" />
        <span className="ml-3 font-mono text-xs text-text-low">
          stratoscan — live market terminal
        </span>
        <span className="ml-auto flex items-center gap-1.5 rounded-full border border-gain/25 bg-gain/10 px-2.5 py-0.5 text-2xs font-semibold text-gain-soft">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gain" aria-hidden="true" />{' '}
          LIVE
        </span>
      </div>

      <div className="grid gap-0 sm:grid-cols-[1.1fr_1fr]">
        {/* Candlestick chart */}
        <div className="relative border-b border-white/[0.08] p-4 sm:border-b-0 sm:border-r">
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-2">
              <span className="font-mono text-xs font-bold text-text-hi">NVDA</span>
              <span className="text-2xs text-text-low">
                {DEMO_MODE ? 'Daily · 42 bars' : 'Daily · live universe'}
              </span>
            </span>
            {DEMO_MODE && (
              <span className="flex items-center gap-1.5 text-2xs text-text-low">
                <span className="h-1.5 w-1.5 rounded-full bg-gain" aria-hidden="true" /> bull
                <span className="h-1.5 w-1.5 rounded-full bg-loss" aria-hidden="true" /> bear
              </span>
            )}
          </div>

          {!DEMO_MODE && (
            <div className="flex h-[200px] items-center justify-center rounded-xl border border-dashed border-white/10 px-4 text-center">
              <div className="space-y-2">
                <p className="text-xs leading-relaxed text-text-mid">
                  Live candle data requires the price-series endpoint.
                </p>
                <p className="text-2xs text-text-low">
                  The ticker and quotes below use real market data from /stocks.
                </p>
              </div>
            </div>
          )}

          {DEMO_MODE && (
            <svg
              viewBox={`0 0 ${w} ${h}`}
              className="w-full"
              role="img"
              aria-label="Animated candlestick chart"
            >
              <defs>
                <linearGradient id="termGlow" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.25" />
                  <stop offset="100%" stopColor="#00d4ff" stopOpacity="0" />
                </linearGradient>
              </defs>
              {/* last-price line */}
              <line
                x1="0"
                y1={y(candles[candles.length - 1].c)}
                x2={w}
                y2={y(candles[candles.length - 1].c)}
                stroke="#00d4ff"
                strokeOpacity="0.35"
                strokeDasharray="4 4"
              />
              {candles.map((c, i) => {
                const x = i * step + step / 2
                return (
                  <g key={i}>
                    <line
                      x1={x}
                      x2={x}
                      y1={y(c.h)}
                      y2={y(c.l)}
                      stroke={c.up ? '#16c784' : '#ea3943'}
                      strokeWidth="1"
                      opacity="0.7"
                    />
                    <rect
                      x={x - step * 0.28}
                      y={Math.min(y(c.o), y(c.c))}
                      width={step * 0.56}
                      height={Math.max(3, Math.abs(y(c.o) - y(c.c)))}
                      rx="1"
                      fill={c.up ? '#16c784' : '#ea3943'}
                      opacity={c.up ? 0.9 : 0.9}
                    />
                  </g>
                )
              })}
              {/* area under last price */}
              <path
                d={`M0,${h - 14} L0,${y(candles[0].c)} ${candles.map((c, i) => `L${i * step + step / 2},${y(c.c)}`).join(' ')} L${w},${h - 14} Z`}
                fill="url(#termGlow)"
                opacity="0.4"
              />
              {/* scanning line */}
              <motion.rect
                x="0"
                y="0"
                width="1.5"
                height={h}
                fill="#00d4ff"
                opacity="0.35"
                animate={{ x: [0, w] }}
                transition={{ duration: 4.5, repeat: Infinity, ease: 'linear' }}
              />
            </svg>
          )}

          {DEMO_MODE && (
            <div className="mt-2 flex items-center justify-between font-mono text-2xs text-text-low">
              <span>volume: 4.2M ▮▮▮▮▮▯▯</span>
              <span>RSI 63 · above SMA-50</span>
            </div>
          )}
        </div>

        {/* Interpreter log */}
        <div className="flex flex-col p-4">
          <div className="min-h-[150px] flex-1 space-y-2 overflow-hidden font-mono text-xs leading-relaxed">
            <p className="text-text-low">
              <span className="text-neon">$</span> stratoscan screen{' '}
              <span className="text-text-hi">{typed}</span>
              <span
                className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-neon align-middle"
                aria-hidden="true"
              />
            </p>
            {typed.length > 6 && (
              <>
                <p className="text-grape-soft">⟶ interpreting “{typed}”…</p>
                {typed.length > 12 && (
                  <>
                    <p className="text-gain-soft">
                      ✓ sector = Technology · market cap &lt; $2B · value screen
                    </p>
                    {typed.length > 24 && (
                      <>
                        <p className="text-gain-soft">
                          ✓ 1,412 candidates → ranked by composite score
                        </p>
                        {typed.length > 40 && (
                          <p className="text-text-mid">
                            ▸ top pick: <span className="text-neon">SOUN</span> — SoundHound AI ·
                            +1.2% today
                          </p>
                        )}
                      </>
                    )}
                  </>
                )}
              </>
            )}
          </div>

          {/* Live quotes strip — real data when API is up, demo drift otherwise */}
          <div className="mt-3 grid grid-cols-4 gap-2 border-t border-white/[0.08] pt-3">
            {liveQuotes.map((q) => (
              <div key={q.sym} className="min-w-0">
                <p className="font-mono text-2xs font-bold text-text-hi">{q.sym}</p>
                <p
                  className={cn(
                    'font-mono text-xs font-semibold tabular',
                    q.chg >= 0 ? 'text-gain-soft' : 'text-loss-soft',
                  )}
                >
                  {q.price !== null ? fmtCurrency(q.price) : '—'}
                </p>
                <p
                  className={cn(
                    'font-mono text-2xs tabular',
                    q.chg >= 0 ? 'text-gain-soft' : 'text-loss-soft',
                  )}
                >
                  {q.price !== null ? (
                    <>{q.chg >= 0 ? '▲' : '▼'} {Math.abs(q.chg).toFixed(2)}%</>
                  ) : 'loading…'}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
