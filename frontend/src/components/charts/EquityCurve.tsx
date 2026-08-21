import { useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { BacktestResultItem } from '@/lib/api/types'
import { fmtCurrencyCompact, fmtDatePlain } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { useContainerWidth } from '@/hooks/useContainerWidth'

interface ChartDatum {
  date: string
  strategy: number
  benchmark?: number
  /** Monte Carlo fan (Tier 3): the p5/median/p95 outcome spread. */
  mcLow?: number
  mcMedian?: number
  mcHigh?: number
  /** Stacked-band delta (mcHigh − mcLow) so recharts can draw the band. */
  mcBand?: number
}

/**
 * Scale the realized equity path so its endpoint lands exactly on a Monte
 * Carlo percentile outcome (start × (1 + pct)). The band keeps the strategy's
 * volatility shape but fans out to the reported 5th/95th percentile totals.
 */
function buildData(item: BacktestResultItem): ChartDatum[] {
  const equity = item.equity_curve ?? []
  const benchRaw = item.benchmark_total_return_pct
  const start = equity[0]?.value ?? 0
  const end = equity[equity.length - 1]?.value ?? start
  // Reconstruct a benchmark line from the reported benchmark total return.
  const benchEnd = start * (1 + (benchRaw ?? 0) / 100)
  const mc = item.monte_carlo
  const hasFan = !!mc?.available && equity.length > 1 && end !== start
  const scale = (pct: number) =>
    end !== start ? (start * (1 + pct) - start) / (end - start) : 0
  return equity.map((p, i) => {
    const t = equity.length > 1 ? i / (equity.length - 1) : 0
    const benchmark = start + (benchEnd - start) * t
    const datum: ChartDatum = {
      date: p.date,
      strategy: p.value,
      benchmark: Number.isFinite(benchmark) ? benchmark : undefined,
    }
    if (hasFan && mc) {
      const delta = p.value - start
      datum.mcLow = start + delta * scale(mc.p5 ?? 0)
      datum.mcMedian = start + delta * scale(mc.median ?? 0)
      datum.mcHigh = start + delta * scale(mc.p95 ?? 0)
      datum.mcBand = (datum.mcHigh ?? 0) - (datum.mcLow ?? 0)
    }
    return datum
  })
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number; color?: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="glass-strong rounded-xl px-4 py-3 shadow-card">
      <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-text-low">
        {fmtDatePlain(label ?? '')}
      </p>
      {payload.map((p, i) => {
        // The MC fan entries are equity-DOLLAR outcomes (p5/median/p95), so
        // they render as currency like the strategy line — not as a % of
        // some arbitrary reference series.
        const label =
          p.name === 'strategy'
            ? 'Strategy'
            : p.name === 'benchmark'
              ? 'SPY benchmark'
              : p.name === 'mcHigh'
                ? 'MC p95'
                : p.name === 'mcMedian'
                  ? 'MC median'
                  : p.name === 'mcLow'
                    ? 'MC p5'
                    : p.name
        return (
          <div key={i} className="flex items-center justify-between gap-6 text-xs">
            <span className="flex items-center gap-2 text-text-mid">
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: p.color }}
                aria-hidden="true"
              />
              {label}
            </span>
            <span className="font-mono font-semibold tabular text-text-hi">
              {fmtCurrencyCompact(p.value)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function EquityCurve({
  item,
  showBenchmark = true,
  height = 320,
}: {
  item: BacktestResultItem
  showBenchmark?: boolean
  height?: number
}) {
  const data = useMemo(() => buildData(item), [item])
  const [mode, setMode] = useState<'equity' | 'drawdown'>('equity')
  // Fallback INSIDE the memo: `item.drawdown_curve ?? []` outside would create
  // a fresh array every render and churn the memo's dependency identity.
  const ddData = useMemo(
    () => (item.drawdown_curve ?? []).map((p) => ({ date: p.date, value: p.value * 100 })),
    [item.drawdown_curve],
  )

  // Only mount the recharts tree once the container has real width. Some
  // layouts (Backtest's mobile + desktop copies) render this chart twice
  // with one CSS-hidden — mounting ResponsiveContainer there would warn
  // "width(0) … should be greater than 0". The wrapper keeps its height so
  // nothing collapses while hidden.
  const { ref, width } = useContainerWidth<HTMLDivElement>()

  const positive = (item.total_return_pct ?? 0) >= 0
  const stroke = positive ? '#00d4ff' : '#ea3943'
  const fillId = positive ? 'gradNeon' : 'gradLoss'
  const hasFan = !!item.monte_carlo?.available && mode === 'equity'

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg bg-white/[0.05] p-0.5">
            {(['equity', 'drawdown'] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-medium transition-colors',
                  mode === m ? 'bg-white/[0.1] text-text-hi' : 'text-text-low hover:text-text-mid',
                )}
                aria-pressed={mode === m}
              >
                {m === 'equity' ? 'Equity curve' : 'Drawdown'}
              </button>
            ))}
          </div>
        </div>
        {mode === 'equity' && showBenchmark && (
          <div className="flex items-center gap-4 text-2xs text-text-low">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-3 rounded-full bg-neon" aria-hidden="true" /> Strategy
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-3 rounded-full bg-grape-soft/70" aria-hidden="true" /> SPY
            </span>
            {hasFan && (
              <span className="flex items-center gap-1.5">
                <span
                  className="h-1.5 w-3 rounded-sm bg-neon/30"
                  aria-hidden="true"
                />{' '}
                5–95% spread
              </span>
            )}
          </div>
        )}
      </div>

      <div ref={ref} style={{ height }} className="-mx-2">
        {width > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            {mode === 'equity' ? (
              <ComposedChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 8 }}>
                <defs>
                  <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={stroke} stopOpacity={0.32} />
                    <stop offset="100%" stopColor={stroke} stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gradBench" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.18} />
                    <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gradFan" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.16} />
                    <stop offset="100%" stopColor="#00d4ff" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  stroke="rgba(255,255,255,0.05)"
                  strokeDasharray="3 6"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tickFormatter={fmtDatePlain}
                  tick={{ fill: '#5d6578', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  minTickGap={48}
                />
                <YAxis
                  tickFormatter={(v: number) => fmtCurrencyCompact(v)}
                  tick={{ fill: '#5d6578', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={64}
                  domain={['auto', 'auto']}
                />
                <Tooltip content={<ChartTooltip />} />
                {showBenchmark && (
                  <Area
                    type="monotone"
                    dataKey="benchmark"
                    stroke="#a78bfa"
                    strokeWidth={1.5}
                    strokeDasharray="5 5"
                    fill="url(#gradBench)"
                    fillOpacity={0.4}
                    name="benchmark"
                  />
                )}
                {hasFan && (
                  <>
                    {/* Stacked Areas: the transparent baseline (mcLow) with the
                        band delta stacked on top draws the p5→p95 fill. */}
                    <Area
                      type="monotone"
                      dataKey="mcLow"
                      stackId="mcFan"
                      stroke="none"
                      fill="none"
                      isAnimationActive={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="mcBand"
                      stackId="mcFan"
                      stroke="none"
                      fill="url(#gradFan)"
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="mcHigh"
                      stroke="#00d4ff"
                      strokeOpacity={0.4}
                      strokeWidth={1}
                      strokeDasharray="3 4"
                      dot={false}
                      isAnimationActive={false}
                      name="mcHigh"
                    />
                    <Line
                      type="monotone"
                      dataKey="mcMedian"
                      stroke="#00d4ff"
                      strokeOpacity={0.55}
                      strokeWidth={1.25}
                      strokeDasharray="1 3"
                      dot={false}
                      isAnimationActive={false}
                      name="mcMedian"
                    />
                    <Line
                      type="monotone"
                      dataKey="mcLow"
                      stroke="#00d4ff"
                      strokeOpacity={0.4}
                      strokeWidth={1}
                      strokeDasharray="3 4"
                      dot={false}
                      isAnimationActive={false}
                      name="mcLow"
                    />
                  </>
                )}
                <Area
                  type="monotone"
                  dataKey="strategy"
                  stroke={stroke}
                  strokeWidth={2.5}
                  fill={`url(#${fillId})`}
                  animationDuration={900}
                  name="strategy"
                />
              </ComposedChart>
            ) : (
              <AreaChart data={ddData} margin={{ top: 6, right: 8, bottom: 0, left: 8 }}>
                <defs>
                  <linearGradient id="gradDD" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#ea3943" stopOpacity={0} />
                    <stop offset="100%" stopColor="#ea3943" stopOpacity={0.3} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  stroke="rgba(255,255,255,0.05)"
                  strokeDasharray="3 6"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tickFormatter={fmtDatePlain}
                  tick={{ fill: '#5d6578', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  minTickGap={48}
                />
                <YAxis
                  tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                  tick={{ fill: '#5d6578', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={44}
                />
                <Tooltip
                  formatter={(v) => [`${Number(v).toFixed(2)}%`, 'Drawdown']}
                  contentStyle={{
                    background: 'rgba(14,14,22,0.9)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 12,
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="#ea3943"
                  strokeWidth={2}
                  fill="url(#gradDD)"
                  animationDuration={900}
                  name="Drawdown"
                />
              </AreaChart>
            )}
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
