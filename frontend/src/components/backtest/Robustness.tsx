import { useMemo } from 'react'
import { Activity, AlertTriangle, CheckCircle2, FlaskConical, Gauge } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type {
  BacktestResultItem,
  ConfidenceIntervalsResult,
  MonteCarloResult,
  ParameterSensitivityResult,
  WalkForwardResult,
} from '@/lib/api/types'
import { fmtPct } from '@/lib/utils'
import { cn } from '@/lib/utils'

type LucideIcon = React.ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>

function BlockShell({
  title,
  icon: Icon,
  children,
}: {
  title: string
  icon: LucideIcon
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-white/[0.03] p-4 ring-1 ring-white/[0.06]">
      <p className="mb-3 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
        <Icon className="h-3.5 w-3.5 text-neon" aria-hidden="true" /> {title}
      </p>
      {children}
    </div>
  )
}

function Stat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'gain' | 'loss'
}) {
  return (
    <div className="rounded-lg bg-white/[0.04] px-2.5 py-2">
      <p className="text-2xs uppercase tracking-wider text-text-low">{label}</p>
      <p
        className={cn(
          'mt-0.5 font-mono text-sm font-bold tabular',
          tone === 'gain' ? 'text-gain-soft' : tone === 'loss' ? 'text-loss-soft' : 'text-text-hi',
        )}
      >
        {value}
      </p>
    </div>
  )
}

function Unavailable({ reason }: { reason?: string | null }) {
  return (
    <p className="text-xs text-text-low">
      Not enough data{reason ? ` — ${reason}` : ''}. Needs more closed trades / return history.
    </p>
  )
}

/** Trade-sequence bootstrap: the outcome spread (5th–95th percentile). */
function MonteCarloBlock({ mc }: { mc: MonteCarloResult }) {
  if (!mc.available) return <Unavailable reason={mc.reason} />
  const { p5, p25, median, p75, p95, prob_positive, trade_count, simulations } = mc
  const buckets = [
    { label: 'p5', v: p5 ?? 0 },
    { label: 'p25', v: p25 ?? 0 },
    { label: 'median', v: median ?? 0 },
    { label: 'p75', v: p75 ?? 0 },
    { label: 'p95', v: p95 ?? 0 },
  ]
  const maxAbs = Math.max(...buckets.map((b) => Math.abs(b.v)), 1e-9)
  const edgeSolid = (prob_positive ?? 0) >= 0.9 && (p5 ?? 0) > 0
  const fragile = (prob_positive ?? 0) < 0.7

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-5 gap-2">
        {buckets.map((b) => (
          <div key={b.label} className="text-center">
            <p className="text-2xs uppercase tracking-wider text-text-low">{b.label}</p>
            <p
              className={cn(
                'font-mono text-sm font-bold tabular',
                b.v >= 0 ? 'text-gain-soft' : 'text-loss-soft',
              )}
            >
              {fmtPct(b.v * 100, 1)}
            </p>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className={cn(
                  'h-full rounded-full',
                  b.v >= 0 ? 'bg-gain' : 'bg-loss',
                )}
                style={{ width: `${(Math.abs(b.v) / maxAbs) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={edgeSolid ? 'gain' : fragile ? 'warning' : 'neutral'}
          className="text-[11px] normal-case"
        >
          {edgeSolid
            ? 'Edge holds at the 5th percentile'
            : fragile
              ? 'Fragile — headline return is mostly luck'
              : 'Mixed signal'}
        </Badge>
        <span className="text-2xs text-text-low">
          {trade_count ?? 0} trades resampled × {simulations ?? 0} simulations ·{' '}
          {fmtPct((prob_positive ?? 0) * 100, 0)} chance of positive outcome
        </span>
      </div>
    </div>
  )
}

/** Bootstrap confidence intervals for Sharpe + max drawdown. */
function ConfidenceBlock({ ci }: { ci: ConfidenceIntervalsResult }) {
  if (!ci.available) return <Unavailable reason={ci.reason} />
  const [sLo, sHi] = ci.sharpe_ci ?? [0, 0]
  const [dLo, dHi] = ci.max_drawdown_ci ?? [0, 0]
  const t = ci.sharpe_t_stat
  const sig = t != null && t >= 2
  const width = Math.max(sHi - sLo, 1e-9)
  const sMid = (sLo + sHi) / 2

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Sharpe CI" value={`${sLo.toFixed(2)} – ${sHi.toFixed(2)}`} />
        <Stat label="Max DD CI" value={`${fmtPct(dLo * 100, 1)} – ${fmtPct(dHi * 100, 1)}`} tone="loss" />
        <Stat
          label="Sharpe t-stat"
          value={t != null ? t.toFixed(2) : '—'}
          tone={sig ? 'gain' : 'loss'}
        />
        <Stat label="Samples" value={String(ci.samples ?? 0)} />
      </div>
      {/* Sharpe CI bar (clamped to ±3 window for a stable scale) */}
      <div>
        <div className="relative mt-1 h-2 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="absolute inset-y-0 rounded-full bg-neon/60"
            style={{ left: `${((sLo + 3) / 6) * 100}%`, width: `${(width / 6) * 100}%` }}
          />
          <div
            className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-text-hi"
            style={{ left: `${((sMid + 3) / 6) * 100}%` }}
          />
        </div>
        <p className="mt-1.5 text-2xs text-text-low">
          {ci.sharpe_sample_note ?? ''} Sharpe t-stat {sig ? '≥ 2 — edge is statistically' : '< 2 — edge not statistically'} distinguishable from noise.
        </p>
      </div>
    </div>
  )
}

/** Walk-forward: in-sample vs out-of-sample + rolling windows. */
function WalkForwardBlock({ wf }: { wf: WalkForwardResult }) {
  if (!wf.available) return <Unavailable reason={wf.reason} />
  const { in_sample: is, out_of_sample: oos, rolling } = wf
  const oosReturn = oos?.total_return ?? 0
  const oosPositive = oosReturn > 0
  const isPositive = (is?.total_return ?? 0) > 0
  const verdict = rolling?.verdict ?? ''

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <div className="rounded-lg bg-white/[0.04] px-2.5 py-2">
          <p className="text-2xs uppercase tracking-wider text-text-low">In-sample</p>
          <p
            className={cn(
              'mt-0.5 font-mono text-sm font-bold tabular',
              isPositive ? 'text-gain-soft' : 'text-loss-soft',
            )}
          >
            {fmtPct((is?.total_return ?? 0) * 100, 1)}
          </p>
          <p className="mt-0.5 text-2xs text-text-low">
            Sharpe {is?.sharpe_ratio?.toFixed(2) ?? '—'} · {is?.total_trades ?? 0} trades
          </p>
        </div>
        <div className="rounded-lg bg-white/[0.04] px-2.5 py-2 ring-1 ring-inset ring-neon/20">
          <p className="text-2xs uppercase tracking-wider text-neon">Out-of-sample</p>
          <p
            className={cn(
              'mt-0.5 font-mono text-sm font-bold tabular',
              oosPositive ? 'text-gain-soft' : 'text-loss-soft',
            )}
          >
            {fmtPct(oosReturn * 100, 1)}
          </p>
          <p className="mt-0.5 text-2xs text-text-low">
            Sharpe {oos?.sharpe_ratio?.toFixed(2) ?? '—'} · {oos?.total_trades ?? 0} trades
          </p>
        </div>
        <div className="rounded-lg bg-white/[0.04] px-2.5 py-2">
          <p className="text-2xs uppercase tracking-wider text-text-low">OOS / IS ratio</p>
          <p className="mt-0.5 font-mono text-sm font-bold tabular text-text-hi">
            {is?.total_return ? `${((oosReturn / is.total_return) * 100).toFixed(0)}%` : '—'}
          </p>
          <p className="mt-0.5 text-2xs text-text-low">
            {rolling?.positive_windows ?? 0}/{rolling?.total_windows ?? 0} rolling windows positive
          </p>
        </div>
        <div className="rounded-lg bg-white/[0.04] px-2.5 py-2">
          <p className="text-2xs uppercase tracking-wider text-text-low">Window range</p>
          <p className="mt-0.5 font-mono text-sm font-bold tabular text-text-hi">
            {fmtPct((rolling?.min_return ?? 0) * 100, 1)} – {fmtPct((rolling?.max_return ?? 0) * 100, 1)}
          </p>
          <p className="mt-0.5 text-2xs text-text-low">median {fmtPct((rolling?.median_return ?? 0) * 100, 1)}</p>
        </div>
      </div>
      {verdict && (
        <Badge
          variant={
            verdict.includes('All windows positive')
              ? 'gain'
              : verdict.includes('All windows negative')
                ? 'loss'
                : 'warning'
          }
          className="text-[11px] normal-case"
        >
          {verdict}
        </Badge>
      )}
    </div>
  )
}

/** RSI × SMA sweep grid with a return heat-map. */
function SensitivityBlock({ ps }: { ps: ParameterSensitivityResult }) {
  const grid = useMemo(() => ps.grid ?? [], [ps.grid])
  const rsiVals = useMemo(() => [...new Set(grid.map((g) => g.rsi))], [grid])
  const smaVals = useMemo(() => [...new Set(grid.map((g) => g.sma))], [grid])
  if (!ps.available) return <Unavailable reason={ps.reason} />
  const best = ps.best
  const worst = ps.worst
  const maxAbs = Math.max(...grid.map((g) => Math.abs(g.total_return)), 1e-9)
  const { overfit_signal, verdict, losing_combos, total_combos } = ps

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-text-low">
              <th className="px-2 py-1.5 font-medium">RSI →</th>
              {smaVals.map((s) => (
                <th key={s} className="px-2 py-1.5 text-center font-medium">
                  SMA {s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {rsiVals.map((r) => (
              <tr key={r}>
                <td className="px-2 py-1.5 font-mono text-text-mid">{r}</td>
                {smaVals.map((s) => {
                  const cell = grid.find((g) => g.rsi === r && g.sma === s)
                  if (!cell) return <td key={s} className="px-2 py-1.5" />
                  const isBest = best && cell.rsi === best.rsi && cell.sma === best.sma
                  const isWorst = worst && cell.rsi === worst.rsi && cell.sma === worst.sma
                  const v = cell.total_return
                  const pct = (Math.abs(v) / maxAbs) * 100
                  return (
                    <td
                      key={s}
                      className={cn(
                        'px-2 py-1.5 text-center font-mono tabular',
                        v >= 0 ? 'text-gain-soft' : 'text-loss-soft',
                        isBest && 'ring-1 ring-inset ring-gain',
                        isWorst && 'ring-1 ring-inset ring-loss',
                      )}
                      style={{
                        background:
                          v >= 0
                            ? `rgba(22,199,132,${0.04 + pct * 0.2})`
                            : `rgba(234,57,67,${0.04 + pct * 0.2})`,
                      }}
                      title={`RSI ${r} · SMA ${s}: ${fmtPct(v * 100, 1)}`}
                    >
                      {fmtPct(v * 100, 0)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={overfit_signal ? 'warning' : 'gain'}
          className="text-[11px] normal-case"
        >
          {overfit_signal ? <AlertTriangle className="mr-1 h-3 w-3" aria-hidden="true" /> : <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden="true" />}
          {verdict ?? ''}
        </Badge>
        <span className="text-2xs text-text-low">
          {losing_combos ?? 0}/{total_combos ?? 0} parameter combos lose money · spread{' '}
          {fmtPct((ps.return_spread ?? 0) * 100, 0)}
        </span>
      </div>
    </div>
  )
}

/**
 * Tier 2 robustness section: turns a single headline number into a
 * distribution — Monte Carlo outcome spread, Sharpe/drawdown confidence
 * intervals, walk-forward IS/OOS, and the parameter-sensitivity heat-map.
 */
export function Robustness({ item }: { item: BacktestResultItem }) {
  const hasAny = Boolean(
    item.monte_carlo ||
      item.confidence_intervals ||
      item.walk_forward ||
      item.parameter_sensitivity,
  )
  if (!hasAny) return null

  return (
    <div className="space-y-3">
      <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-text-low">
        <FlaskConical className="h-3.5 w-3.5 text-neon" aria-hidden="true" />
        Robustness — is this edge real?
      </p>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {item.monte_carlo && (
          <BlockShell title="Monte Carlo outcome spread" icon={Activity}>
            <MonteCarloBlock mc={item.monte_carlo} />
          </BlockShell>
        )}
        {item.confidence_intervals && (
          <BlockShell title="Confidence intervals" icon={Gauge}>
            <ConfidenceBlock ci={item.confidence_intervals} />
          </BlockShell>
        )}
        {item.walk_forward && (
          <BlockShell title="Walk-forward — IS vs OOS" icon={FlaskConical}>
            <WalkForwardBlock wf={item.walk_forward} />
          </BlockShell>
        )}
        {item.parameter_sensitivity && (
          <BlockShell title="Parameter sensitivity" icon={Activity}>
            <SensitivityBlock ps={item.parameter_sensitivity} />
          </BlockShell>
        )}
      </div>
    </div>
  )
}
