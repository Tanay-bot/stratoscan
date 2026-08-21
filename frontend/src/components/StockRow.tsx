import { Link } from 'react-router-dom'
import { ArrowDownRight, ArrowUpRight, Clock } from 'lucide-react'
import type { StockResult } from '@/lib/api/types'
import { fmtCurrency, fmtMarketCap, fmtPct } from '@/lib/utils'
import { cn } from '@/lib/utils'

function DataAgeBadge({ stock }: { stock: StockResult }) {
  if (!stock.is_stale && stock.data_age_minutes == null) return null
  const mins = stock.data_age_minutes
  const label =
    mins == null
      ? 'Age unknown'
      : mins < 60
        ? `${Math.max(1, Math.round(mins))}m old`
        : mins < 1440
          ? `${Math.round(mins / 60)}h old`
          : `${Math.round(mins / 1440)}d old`
  return (
    <span
      title={`Last refreshed ${mins != null ? `${Math.round(mins)} minutes ago` : 'unknown'}`}
      className={cn(
        'flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium',
        stock.is_stale
          ? 'bg-amber-400/[0.12] text-amber-300 ring-1 ring-inset ring-amber-400/30'
          : 'bg-gain/[0.1] text-gain-soft ring-1 ring-inset ring-gain/25',
      )}
    >
      <Clock className="h-2.5 w-2.5" aria-hidden="true" />
      {label}
    </span>
  )
}

function RSI({ value }: { value?: number | null }) {
  if (value == null) return <span className="text-text-low">—</span>
  const pct = Math.min(100, Math.max(0, value))
  const tone = value >= 70 ? 'text-loss-soft' : value <= 30 ? 'text-gain-soft' : 'text-neon'
  return (
    <div className="flex w-20 items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
        <div
          className={cn(
            'h-full rounded-full',
            value >= 70 ? 'bg-loss' : value <= 30 ? 'bg-gain' : 'bg-neon',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={cn('font-mono text-xs font-semibold tabular', tone)}>
        {value.toFixed(0)}
      </span>
    </div>
  )
}

function SentimentDot({ score }: { score?: number | null }) {
  if (score == null) return null
  const tone = score >= 0.15 ? 'bg-gain' : score <= -0.15 ? 'bg-loss' : 'bg-white/40'
  const label =
    score >= 0.15 ? 'Bullish sentiment' : score <= -0.15 ? 'Bearish sentiment' : 'Neutral sentiment'
  return (
    <span className="flex items-center gap-1.5" title={label}>
      <span className={cn('h-1.5 w-1.5 rounded-full', tone)} aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  )
}

export function StockRow({ stock, index }: { stock: StockResult; index: number }) {
  const up = (stock.price_change_pct ?? 0) >= 0
  const score = stock.score ?? 0

  return (
    <Link
      to={`/app/stocks/${stock.symbol}`}
      data-stock-row
      className={cn(
        'glass-hover group grid grid-cols-2 items-center gap-x-4 gap-y-3 rounded-2xl p-4 sm:grid-cols-12 sm:gap-2',
      )}
    >
      {/* Symbol + company */}
      <div className="flex min-w-0 items-center gap-3 sm:col-span-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-neon/15 to-grape/20 font-mono text-xs font-bold text-neon shadow-[inset_0_0_0_1px_rgba(0,212,255,0.2)]">
          {index + 1}
        </span>
        <div className="min-w-0">
          <p className="flex items-center gap-2">
            <span className="font-mono text-sm font-bold tracking-wide text-text-hi">
              {stock.symbol}
            </span>
            <DataAgeBadge stock={stock} />
          </p>
          <p className="truncate text-xs text-text-low">{stock.company_name}</p>
        </div>
      </div>

      {/* Price + change */}
      <div className="text-right sm:col-span-2">
        <p className="font-mono text-sm font-semibold tabular text-text-hi">
          {fmtCurrency(stock.current_price)}
        </p>
        <p
          className={cn(
            'flex items-center justify-end gap-0.5 font-mono text-xs font-semibold tabular',
            up ? 'text-gain-soft' : 'text-loss-soft',
          )}
        >
          {up ? (
            <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ArrowDownRight className="h-3 w-3" aria-hidden="true" />
          )}
          {fmtPct(stock.price_change_pct)}
        </p>
      </div>

      {/* Market cap */}
      <div className="hidden sm:col-span-2 sm:block">
        <p className="font-mono text-xs text-text-mid tabular">{fmtMarketCap(stock.market_cap)}</p>
      </div>

      {/* P/E */}
      <div className="hidden sm:col-span-2 sm:block">
        <p className="font-mono text-xs tabular text-text-mid">
          {stock.pe_ratio?.toFixed(1) ?? '—'}
        </p>
      </div>

      {/* RSI momentum */}
      <div className="hidden sm:col-span-2 sm:block">
        <RSI value={stock.rsi_14} />
      </div>

      {/* Score + sentiment */}
      <div className="col-span-2 flex items-center justify-end gap-3 sm:col-span-1">
        <SentimentDot score={stock.sentiment?.company_news_score ?? stock.sentiment?.buzz} />
        <span
          className={cn(
            'rounded-lg px-2 py-1 font-mono text-xs font-bold tabular',
            score >= 70
              ? 'bg-neon/10 text-neon shadow-[inset_0_0_0_1px_rgba(0,212,255,0.25)]'
              : score >= 45
                ? 'bg-grape/10 text-grape-soft shadow-[inset_0_0_0_1px_rgba(124,58,237,0.3)]'
                : 'bg-white/[0.05] text-text-mid',
          )}
        >
          {score.toFixed(0)}
        </span>
        <span className="hidden text-2xs text-text-low sm:block">score</span>
      </div>
    </Link>
  )
}
