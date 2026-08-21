import { useEffect, useMemo, useRef, useState } from 'react'

/** Deterministic pseudo-random float for layout jitter */
function seeded(seed: number) {
  let s = seed
  return () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }
}

const REDUCED =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const rng = seeded(2026)

type Candle = { id: number; o: number; h: number; l: number; c: number }

const MAX_CANDLES = 47
const FLOOR_W = 1200
const FLOOR_H = 260
const M = 16
const STEP = (FLOOR_W - M * 2) / (MAX_CANDLES - 1)
const BODY_W = 7
const BEAM_X = M + (MAX_CANDLES - 2) * STEP
const TOP = 16
const BAND = FLOOR_H - TOP - 20 // chart band height

let candleId = 0

function makeCandle(prevClose: number, idx: number): Candle {
  // gentle sin regime + occasional momentum bursts → reads like a real tape
  const regime = Math.sin(idx * 0.34) * 0.55
  const burst = rng() < 0.16 ? (rng() - 0.32) * 2.8 : 0
  const move = regime + (rng() - 0.5) * 1.1 + burst
  const o = prevClose + (rng() - 0.5) * 0.7
  const c = o + move
  return {
    id: candleId++,
    o,
    h: Math.max(o, c) + rng() * 0.85,
    l: Math.min(o, c) - rng() * 0.85,
    c,
  }
}

function seedCandles(): Candle[] {
  const out: Candle[] = []
  let close = 128 + (rng() - 0.5) * 26
  for (let i = 0; i < MAX_CANDLES - 1; i++) {
    out.push(makeCandle(close, i))
    close = out[out.length - 1].c
  }
  return out
}

function useInView<T extends HTMLElement>(threshold = 0.1) {
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true)
          obs.disconnect()
        }
      },
      { threshold },
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [threshold])
  return { ref, inView }
}

export function HeroViz() {
  const { ref, inView } = useInView<HTMLDivElement>(0.1)
  const prevCloseRef = useRef(128)
  const [candles, setCandles] = useState<Candle[]>(() => {
    const s = seedCandles()
    prevCloseRef.current = s[s.length - 1].c
    return s
  })
  const [shift, setShift] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const latestRef = useRef<Candle | null>(null)
  const idxRef = useRef(MAX_CANDLES - 1)
  // Guards phase-2 (drop + snap-back) so it fires exactly once per tick — via
  // the tape's transitionend, with the fallback timeout only as a safety net.
  const phase2Done = useRef(false)

  const live = inView && !REDUCED

  // The tape scroll runs whenever the hero is visible — a slow, gentle slide like a real
  // chart (old candles exit left, new ones push in from the right). This one stays on for
  // everyone: it's the same mild horizontal drift as the typewriter's text reveal. The
  // strobe-like flash, pulsing beam and particle drift stay gated behind `live`.
  useEffect(() => {
    if (!inView) return
    const t = window.setInterval(() => {
      const nc = makeCandle(prevCloseRef.current, idxRef.current)
      idxRef.current += 1
      prevCloseRef.current = nc.c
      latestRef.current = nc
      setCandles((prev) => [...prev, nc].slice(-MAX_CANDLES))
      setShift(1)
    }, 1200)
    return () => window.clearInterval(t)
  }, [inView])

  // One-shot left→right draw-in: the chart traces itself in the first time it appears.
  useEffect(() => {
    if (inView) setRevealed(true)
  }, [inView])

  // Phase 2 of the tape slide: drop the now-off-screen oldest candle and snap
  // back. The exact trigger is the tape group's transitionend (browser-clock,
  // zero drift — the old fixed setTimeout could land a frame BEFORE the CSS
  // transition finished, flickering the whole chart backward every tick). A
  // guarded fallback timeout covers tabs where transitions get paused.
  useEffect(() => {
    if (shift !== 1) {
      phase2Done.current = false
      return
    }
    const t = window.setTimeout(() => {
      if (!phase2Done.current) {
        phase2Done.current = true
        setCandles((prev) => prev.slice(1))
        setShift(0)
      }
    }, 700)
    return () => window.clearTimeout(t)
  }, [shift])

  // Auto-scale price band. The raw window (min/max of the visible candles) is
  // recomputed every tick; easing toward it — expanding instantly when a new
  // extreme appears (never clip a candle) but gliding back when it leaves —
  // removes the old hard re-scale that made the whole chart jump vertically.
  const rawBand = useMemo(() => {
    let lo = Infinity
    let hi = -Infinity
    for (const k of candles) {
      if (k.l < lo) lo = k.l
      if (k.h > hi) hi = k.h
    }
    const pad = (hi - lo) * 0.12 + 0.5
    return { min: lo - pad, max: hi + pad }
  }, [candles])

  const [band, setBand] = useState(() => rawBand)

  useEffect(() => {
    setBand((prev) => {
      // Expand instantly to contain a new extreme; glide back slowly when it
      // exits the window — so the axis never clips AND never hard-jumps.
      // NOTE: this effect runs twice per tick (interval append + phase-2
      // slice), so 0.35 compounds to ~0.58 effective per 1.2s tick — a
      // gentle glide that fully converges within a few candles.
      const max = rawBand.max > prev.max ? rawBand.max : prev.max + (rawBand.max - prev.max) * 0.35
      const min = rawBand.min < prev.min ? rawBand.min : prev.min + (rawBand.min - prev.min) * 0.35
      if (Math.abs(max - prev.max) < 0.01 && Math.abs(min - prev.min) < 0.01) return prev
      return { min, max }
    })
  }, [rawBand])

  const { min, max } = band
  const y = (p: number) => TOP + (1 - (p - min) / (max - min)) * BAND

  const closeD = candles
    .map((k, i) => `${i === 0 ? 'M' : 'L'}${(M + i * STEP).toFixed(1)},${y(k.c).toFixed(1)}`)
    .join(' ')
  const areaD = `M 0,${(TOP + BAND).toFixed(0)} ${closeD.replace(/^M/, 'L')} L${(M + (candles.length - 1) * STEP).toFixed(1)},${(TOP + BAND).toFixed(0)} Z`
  const last = candles[candles.length - 1]

  const particles = useMemo(() => {
    const r = seeded(9)
    return Array.from({ length: 22 }, (_, i) => ({
      left: r() * 100,
      top: 3 + r() * 62,
      size: 1 + r() * 1.9,
      dur: 13 + r() * 15,
      delay: r() * 12,
      purple: i % 5 === 0,
    }))
  }, [])

  const grid = [0.25, 0.5, 0.75].map((f) => {
    const gy = TOP + f * BAND
    return { y: gy, price: min + (max - min) * (1 - f) }
  })

  return (
    <div ref={ref} className="relative h-full w-full overflow-hidden" aria-hidden="true">
      {/* Grid backdrop */}
      <div className="grid-backdrop absolute inset-0" />

      {/* Soft nebula pool the market floor rises from */}
      <div className="absolute bottom-0 left-1/2 h-56 w-[75%] -translate-x-1/2 rounded-full bg-neon/[0.06] blur-[110px]" />

      {/* Particle sky — slow upward drift (gated behind reduced-motion with the rest) */}
      {particles.map((p, i) => (
        <span
          key={i}
          data-particle
          className="absolute rounded-full"
          style={{
            left: `${p.left}%`,
            top: `${p.top}%`,
            width: p.size,
            height: p.size,
            background: p.purple ? 'rgba(167,139,250,0.85)' : 'rgba(0,212,255,0.85)',
            boxShadow: p.purple ? '0 0 6px rgba(167,139,250,0.7)' : '0 0 6px rgba(0,212,255,0.7)',
            animation: live ? `float-up ${p.dur}s linear ${p.delay}s infinite` : undefined,
            opacity: live ? undefined : 0.5,
          }}
        />
      ))}

      {/* Market floor: live candlestick tape */}
      <div
        className="absolute inset-x-0 bottom-0 h-[34vh] md:h-[40vh]"
        style={{
          maskImage: 'linear-gradient(to top, black 40%, transparent 97%)',
          WebkitMaskImage: 'linear-gradient(to top, black 40%, transparent 97%)',
        }}
      >
        <div className="relative h-full w-full">
          <svg
            viewBox={`0 0 ${FLOOR_W} ${FLOOR_H}`}
            preserveAspectRatio="none"
            className="h-full w-full"
          >
            <defs>
              <linearGradient id="heroCloseGrad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.12" />
                <stop offset="55%" stopColor="#00d4ff" stopOpacity="0.7" />
                <stop offset="100%" stopColor="#a78bfa" stopOpacity="0.95" />
              </linearGradient>
              <linearGradient id="heroAreaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.14" />
                <stop offset="100%" stopColor="#00d4ff" stopOpacity="0" />
              </linearGradient>
              <linearGradient id="beamGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00d4ff" stopOpacity="0" />
                <stop offset="50%" stopColor="#00d4ff" stopOpacity="0.55" />
                <stop offset="100%" stopColor="#00d4ff" stopOpacity="0" />
              </linearGradient>
              <radialGradient id="glowGrad">
                <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.5" />
                <stop offset="100%" stopColor="#00d4ff" stopOpacity="0" />
              </radialGradient>
              <clipPath id="heroReveal">
                <rect
                  data-reveal
                  x={0}
                  y={0}
                  width={FLOOR_W}
                  height={FLOOR_H}
                  style={
                    revealed
                      ? { animation: `hero-reveal 1050ms cubic-bezier(0.4, 0, 0.2, 1) both` }
                      : { width: 0 }
                  }
                />
              </clipPath>
            </defs>

            {/* Chart surface draws in left→right on first view, then scrolls live */}
            <g clipPath="url(#heroReveal)">
              {/* Price grid + baseline */}
              {grid.map((g, i) => (
                <line
                  key={i}
                  data-grid
                  x1={M}
                  x2={FLOOR_W - M}
                  y1={g.y}
                  y2={g.y}
                  stroke="rgba(255,255,255,0.05)"
                  strokeWidth="1"
                  strokeDasharray="2 6"
                />
              ))}
              <line
                x1={M}
                x2={FLOOR_W - M}
                y1={TOP + BAND}
                y2={TOP + BAND}
                stroke="rgba(255,255,255,0.1)"
                strokeWidth="1.2"
              />

              {/* Tape: candles + close-line + area fill, slides left each tick.
                  Phase 2 (drop + snap) fires from transitionend — browser-exact,
                  no timer drift — so the reset is invisible by construction. */}
              <g
                data-tape
                onTransitionEnd={(e) => {
                  if (e.propertyName !== 'transform' || shift !== 1 || phase2Done.current) return
                  phase2Done.current = true
                  setCandles((prev) => prev.slice(1))
                  setShift(0)
                }}
                style={{
                  transform: `translateX(${(-shift * STEP).toFixed(2)}px)`,
                  transition: shift === 1 ? 'transform 430ms cubic-bezier(0.4, 0, 0.2, 1)' : 'none',
                  // Promote the continuous slide to its own compositor layer —
                  // no repaint jank on the 430ms translate.
                  willChange: 'transform',
                }}
              >
                {candles.map((k, i) => {
                  const x = M + i * STEP
                  const up = k.c >= k.o
                  const color = up ? '#16c784' : '#ea3943'
                  const bodyY = y(Math.max(k.o, k.c))
                  const bodyH = Math.max(1.2, Math.abs(y(k.o) - y(k.c)))
                  const isNew = k === latestRef.current
                  return (
                    <g key={k.id}>
                      <line
                        x1={x}
                        x2={x}
                        y1={y(k.h)}
                        y2={y(k.l)}
                        stroke={color}
                        strokeOpacity="0.5"
                        strokeWidth="1.1"
                        style={isNew && live ? { animation: 'wick-in 380ms ease-out' } : undefined}
                      />
                      <rect
                        data-candle
                        x={x - BODY_W / 2}
                        y={bodyY}
                        width={BODY_W}
                        height={bodyH}
                        rx="1.2"
                        fill={color}
                        fillOpacity="0.55"
                        style={
                          isNew && live
                            ? {
                                transformBox: 'fill-box',
                                transformOrigin: '50% 100%',
                                animation: 'candle-in 460ms cubic-bezier(0.22, 1, 0.36, 1) both',
                              }
                            : undefined
                        }
                      />
                    </g>
                  )
                })}
                <path data-area d={areaD} fill="url(#heroAreaGrad)" />
                <path
                  data-closeline
                  d={closeD}
                  fill="none"
                  stroke="url(#heroCloseGrad)"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ filter: 'drop-shadow(0 0 4px rgba(0,212,255,0.35))' }}
                />
              </g>

              {/* Now-beam + trade-flash live INSIDE the reveal clip so they only appear
                  once the draw-in sweep reaches the right edge (no dot on empty space) */}
              <line
                data-beam
                x1={BEAM_X}
                x2={BEAM_X}
                y1={TOP - 6}
                y2={TOP + BAND + 6}
                stroke="url(#beamGrad)"
                strokeWidth="1.4"
                style={
                  live ? { animation: 'beam-pulse 2.6s ease-in-out infinite' } : { opacity: 0.35 }
                }
              />

              {/* Radial trade-flash at the newest candle's close, remounts each tick */}
              {live && last && (
                <circle
                  key={last.id}
                  data-flash
                  cx={BEAM_X}
                  cy={y(last.c)}
                  r={26}
                  fill="url(#glowGrad)"
                  style={{
                    transformBox: 'fill-box',
                    transformOrigin: 'center',
                    animation: 'trade-flash 950ms cubic-bezier(0.22, 1, 0.36, 1) forwards',
                  }}
                />
              )}
            </g>
          </svg>

          {/* Price labels as unstretched HTML (svg is preserveAspectRatio=none) */}
          {grid.map((g, i) => (
            <span
              key={i}
              data-gridlabel
              className="pointer-events-none absolute font-mono text-[9.5px] tabular text-text-low"
              style={{
                right: `${((M + 2) / FLOOR_W) * 100}%`,
                top: `${(g.y / FLOOR_H) * 100}%`,
                transform: 'translateY(-50%)',
                opacity: 0.45,
              }}
            >
              ${Math.round(g.price)}
            </span>
          ))}
        </div>
      </div>

      <style>{`
        @keyframes hero-reveal {
          from { width: 0px; }
          to { width: ${FLOOR_W}px; }
        }
        @keyframes candle-in {
          from { transform: scaleY(0.1); opacity: 0.4; }
          to { transform: scaleY(1); opacity: 1; }
        }
        @keyframes wick-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes trade-flash {
          0% { transform: scale(0.25); opacity: 0.85; }
          100% { transform: scale(1.7); opacity: 0; }
        }
        @keyframes beam-pulse {
          0%, 100% { opacity: 0.35; }
          50% { opacity: 0.85; }
        }
        @keyframes float-up {
          0% { transform: translateY(0); opacity: 0; }
          12% { opacity: 0.55; }
          85% { opacity: 0.35; }
          100% { transform: translateY(-110px); opacity: 0; }
        }
      `}</style>
    </div>
  )
}
