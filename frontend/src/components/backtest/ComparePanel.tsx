import { useMemo } from 'react'
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { GitCompareArrows, Trophy, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { BacktestResponse } from '@/lib/api/types'
import { fmtDatePlain, fmtPct } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { useContainerWidth } from '@/hooks/useContainerWidth'

interface Point {
  date: string
  value: number
}

/** Normalize an equity curve to % return (start = 0) so two strategies with
 * the same $100k pool are directly comparable. */
function toReturnSeries(curve: Point[]): { date: string; value: number }[] {
  if (!curve.length) return []
  const start = curve[0].value
  if (!start) return curve.map((p) => ({ date: p.date, value: 0 }))
  return curve.map((p) => ({ date: p.date, value: (p.value / start - 1) * 100 }))
}

/** Combined-series accessor: portfolio curve when present, else first symbol. */
function equitySeries(res: BacktestResponse): Point[] {
  if (res.portfolio?.equity_curve?.length) return res.portfolio.equity_curve
  const first = Object.values(res.results)[0]
  return first?.equity_curve ?? []
}

function metric(
  res: BacktestResponse,
  pick: (r: NonNullable<BacktestResponse['results'][string]>) => number | null | undefined,
): number | null {
  const all = Object.values(res.results)
  if (!all.length) return null
  if (res.portfolio) {
    if (pick === pickReturn) return res.portfolio.total_return_pct
    if (pick === pickSharpe) return res.portfolio.sharpe_ratio ?? null
    if (pick === pickMaxDD) return res.portfolio.max_drawdown_pct ?? null
  }
  const top = all[0]
  const v = pick(top)
  return v == null ? null : Number(v)
}

const pickReturn = (r: { total_return_pct?: number | null }) => r.total_return_pct
const pickSharpe = (r: { sharpe_ratio?: number | null }) => r.sharpe_ratio
const pickMaxDD = (r: { max_drawdown_pct?: number | null }) => r.max_drawdown_pct
const pickTrades = (r: { total_trades?: number }) => r.total_trades
const pickWin = (r: { win_rate_pct?: number | null }) => r.win_rate_pct

export function ComparePanel({
  baseline,
  challenger,
  onClear,
}: {
  baseline: BacktestResponse
  challenger: BacktestResponse
  onClear: () => void
}) {
  const baseSeries = useMemo(() => toReturnSeries(equitySeries(baseline)), [baseline])
  const chalSeries = useMemo(() => toReturnSeries(equitySeries(challenger)), [challenger])

  const combined = useMemo(() => {
    const dates = [...new Set([...baseSeries.map((p) => p.date), ...chalSeries.map((p) => p.date)])]
      .sort()
    const baseMap = new Map(baseSeries.map((p) => [p.date, p.value]))
    const chalMap = new Map(chalSeries.map((p) => [p.date, p.value]))
    return dates.map((date) => ({
      date,
      baseline: baseMap.get(date),
      challenger: chalMap.get(date),
    }))
  }, [baseSeries, chalSeries])

  const { ref, width } = useContainerWidth<HTMLDivElement>()

  const rows = [
    { label: 'Total return', base: metric(baseline, pickReturn), chal: metric(challenger, pickReturn), fmt: (v: number) => fmtPct(v), better: 'higher' },
    { label: 'Sharpe ratio', base: metric(baseline, pickSharpe), chal: metric(challenger, pickSharpe), fmt: (v: number) => v.toFixed(2), better: 'higher' },
    { label: 'Max drawdown', base: metric(baseline, pickMaxDD), chal: metric(challenger, pickMaxDD), fmt: (v: number) => fmtPct(v), better: 'higher' },
    { label: 'Win rate', base: metric(baseline, pickWin), chal: metric(challenger, pickWin), fmt: (v: number) => fmtPct(v, 0, false), better: 'higher' },
    { label: 'Trades', base: metric(baseline, pickTrades), chal: metric(challenger, pickTrades), fmt: (v: number) => String(v), better: '—' },
  ] as const

  const baseTotal = rows[0].base
  const chalTotal = rows[0].chal
  const winner =
    baseTotal == null || chalTotal == null
      ? null
      : baseTotal > chalTotal
        ? 'baseline'
        : chalTotal > baseTotal
          ? 'challenger'
          : null

  const sharedSymbols = Object.keys(baseline.results).filter((s) => s in challenger.results)

  return (
    <div className="rounded-2xl border border-grape/25 bg-gradient-to-b from-grape/[0.06] to-transparent p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
          <GitCompareArrows className="h-3.5 w-3.5 text-grape-soft" aria-hidden="true" />
          Compare — same {sharedSymbols.length || Object.keys(baseline.results).length} symbol set
        </p>
        <div className="flex items-center gap-2">
          {winner && (
            <Badge variant="gain" className="text-[11px] normal-case">
              <Trophy className="mr-1 h-3 w-3" aria-hidden="true" />
              {winner === 'baseline' ? 'Baseline wins' : 'Challenger wins'}
            </Badge>
          )}
          <Button variant="ghost" size="sm" onClick={onClear} aria-label="Clear comparison">
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </div>

      {/* Overlaid %-return curves */}
      <div ref={ref} className="-mx-2 h-56">
        {width > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={combined} margin={{ top: 6, right: 8, bottom: 0, left: 8 }}>
              <defs>
                <linearGradient id="gradBase" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="#00d4ff" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gradChal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(255,255,255,0.05)" strokeDasharray="3 6" vertical={false} />
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
                formatter={(v, name) => [
                  fmtPct(Number(v), 2),
                  name === 'baseline' ? 'Baseline' : 'Challenger',
                ]}
                contentStyle={{
                  background: 'rgba(14,14,22,0.92)',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 12,
                  fontSize: 12,
                }}
              />
              <Legend
                formatter={(value) => (
                  <span className="text-xs text-text-mid">
                    {value === 'baseline' ? 'Baseline' : 'Challenger'}
                  </span>
                )}
              />
              <Line
                type="monotone"
                dataKey="baseline"
                stroke="#00d4ff"
                strokeWidth={2.5}
                dot={false}
                connectNulls
                animationDuration={700}
              />
              <Line
                type="monotone"
                dataKey="challenger"
                stroke="#a78bfa"
                strokeWidth={2.5}
                dot={false}
                connectNulls
                strokeDasharray="6 4"
                animationDuration={700}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Metric table */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-text-low">
              <th className="px-3 py-2 font-medium">Metric</th>
              <th className="px-3 py-2 text-right font-medium">
                <span className="text-neon">Baseline</span>
              </th>
              <th className="px-3 py-2 text-right font-medium">
                <span className="text-grape-soft">Challenger</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {rows.map((row) => {
              const base = row.base
              const chal = row.chal
              const baseWin = base != null && chal != null && row.better === 'higher' && base > chal
              const chalWin = base != null && chal != null && row.better === 'higher' && chal > base
              return (
                <tr key={row.label}>
                  <td className="px-3 py-2 text-text-mid">{row.label}</td>
                  <td
                    className={cn(
                      'px-3 py-2 text-right font-mono tabular text-text-hi',
                      baseWin && 'bg-gain/[0.06] text-gain-soft',
                    )}
                  >
                    {base == null ? '—' : row.fmt(base)}
                  </td>
                  <td
                    className={cn(
                      'px-3 py-2 text-right font-mono tabular text-text-hi',
                      chalWin && 'bg-gain/[0.06] text-gain-soft',
                    )}
                  >
                    {chal == null ? '—' : row.fmt(chal)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {sharedSymbols.length > 0 && (
        <p className="mt-3 text-2xs text-text-low">
          Same-symbol comparison across {sharedSymbols.length} shared picks:{' '}
          {sharedSymbols.join(', ')}
        </p>
      )}
    </div>
  )
}
