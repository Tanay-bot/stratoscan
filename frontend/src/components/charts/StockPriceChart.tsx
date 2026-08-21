import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { priceHistory } from '@/lib/api/demo'
import { DEMO_MODE } from '@/lib/api/client'
import { fmtCurrency, fmtCurrencyCompact, fmtDatePlain, fmtPct } from '@/lib/utils'
import type { StockResult } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { useContainerWidth } from '@/hooks/useContainerWidth'

/**
 * Price chart. Two modes:
 *
 * - Demo mode: a synthetic historical curve from the demo feed (the UI is
 *   demoable without a backend, and the curve is labeled as such).
 *
 * - Live mode: the API exposes a price *snapshot*, not a price series, so we
 *   render an honest "52-week range position" chart from real fields
 *   (current price vs 52w high/low, SMA-50/200 markers, % off the high).
 *   We never invent a historical curve against real API data.
 */
export function StockPriceChart({ stock }: { stock: StockResult }) {
  const { symbol, sma_50: sma50Prop } = stock

  // Hooks must run unconditionally (DEMO_MODE is a build-time constant, but a
  // conditional useMemo is a fragile hook-order footgun — hoisted above the
  // early return so the rule is never violated).
  const data = useMemo(() => {
    const raw = priceHistory(symbol, 120)
    return raw.map((p, i) => ({
      date: p.date,
      price: p.value,
      sma50: sma50Prop != null && i > 20 ? p.value * 1.02 : undefined,
    }))
  }, [symbol, sma50Prop])

  // Same width gate as EquityCurve: never mount ResponsiveContainer inside a
  // zero-width (e.g. CSS-hidden responsive) container — recharts warns
  // "width(0) … should be greater than 0" there. Hoisted above the early
  // return so the hook order is stable across renders (rules-of-hooks).
  const { ref, width } = useContainerWidth<HTMLDivElement>()

  if (!DEMO_MODE) {
    return <LiveRangeSnapshot stock={stock} />
  }

  const positive = data.length > 1 && data[data.length - 1].price >= data[0].price

  return (
    <div ref={ref} className="h-[300px] -mx-1">
      {width > 0 && (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 8 }}>
            <defs>
              <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={positive ? '#00d4ff' : '#ea3943'} stopOpacity={0.28} />
                <stop offset="100%" stopColor={positive ? '#00d4ff' : '#ea3943'} stopOpacity={0} />
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
              tickFormatter={(v: number) => fmtCurrencyCompact(v)}
              tick={{ fill: '#5d6578', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={64}
              domain={['auto', 'auto']}
            />
            <Tooltip
              formatter={(v, name) => [
                fmtCurrencyCompact(Number(v)),
                name === 'price' ? 'Price' : 'SMA-50',
              ]}
              contentStyle={{
                background: 'rgba(14,14,22,0.92)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 12,
                fontSize: 12,
                backdropFilter: 'blur(12px)',
              }}
              labelFormatter={(l) => fmtDatePlain(String(l))}
            />
            <Area
              type="monotone"
              dataKey="sma50"
              stroke="#a78bfa"
              strokeWidth={1.5}
              strokeDasharray="5 5"
              fill="none"
              animationDuration={700}
              name="sma50"
            />
            <Area
              type="monotone"
              dataKey="price"
              stroke={positive ? '#00d4ff' : '#ea3943'}
              strokeWidth={2.5}
              fill="url(#priceFill)"
              animationDuration={900}
              name="price"
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

/** Live-mode snapshot: current price's position inside the real 52-week range. */
function LiveRangeSnapshot({ stock }: { stock: StockResult }) {
  const { low_52w, high_52w, current_price, sma_50, sma_200, price_change_pct, pct_off_52w_high } =
    stock

  const price = current_price
  const low = low_52w ?? null
  const high = high_52w ?? null

  // Position of the current price (0..1) inside the range when both bounds exist.
  const pos =
    price != null && low != null && high != null && high > low
      ? Math.min(1, Math.max(0, (price - low) / (high - low)))
      : null

  // Marker positions for SMA-50 / SMA-200 (only if inside the visible range).
  const marker = (v?: number | null) =>
    v != null && low != null && high != null && high > low
      ? Math.min(1, Math.max(0, (v - low) / (high - low)))
      : null

  const sma50Pos = marker(sma_50)
  const sma200Pos = marker(sma_200)

  const up = (price_change_pct ?? 0) >= 0

  return (
    <div className="flex h-[300px] flex-col justify-center">
      {/* Range bar */}
      <div
        className="relative h-2 rounded-full bg-gradient-to-r from-loss/50 via-white/15 to-gain/50"
        aria-hidden="true"
      >
        {/* SMA markers */}
        {sma50Pos != null && (
          <span
            className="absolute top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-grape-soft/80"
            style={{ left: `${sma50Pos * 100}%` }}
            title={`SMA-50 ${fmtCurrency(sma_50)}`}
          />
        )}
        {sma200Pos != null && (
          <span
            className="absolute top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-amber-400/70"
            style={{ left: `${sma200Pos * 100}%` }}
            title={`SMA-200 ${fmtCurrency(sma_200)}`}
          />
        )}
        {/* Current price dot */}
        {pos != null && (
          <span
            className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink bg-neon shadow-[0_0_12px_rgba(0,212,255,0.8)]"
            style={{ left: `${pos * 100}%` }}
          />
        )}
      </div>

      {/* Bounds labels */}
      <div className="mt-3 flex items-center justify-between font-mono text-xs text-text-mid">
        <span>
          52w low <span className="tabular text-text-hi">{fmtCurrency(low)}</span>
        </span>
        <span>
          52w high <span className="tabular text-text-hi">{fmtCurrency(high)}</span>
        </span>
      </div>

      {/* Summary row */}
      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="glass-hover rounded-xl p-3">
          <p className="text-2xs uppercase tracking-wider text-text-low">Price</p>
          <p className="mt-1 font-mono text-sm font-bold tabular text-text-hi">
            {fmtCurrency(price)}
          </p>
          <p
            className={cn(
              'font-mono text-xs font-semibold tabular',
              up ? 'text-gain-soft' : 'text-loss-soft',
            )}
          >
            {fmtPct(price_change_pct)}
          </p>
        </div>
        <div className="glass-hover rounded-xl p-3">
          <p className="text-2xs uppercase tracking-wider text-text-low">Range position</p>
          <p className="mt-1 font-mono text-sm font-bold tabular text-text-hi">
            {pos != null ? `${(pos * 100).toFixed(0)}%` : '—'}
          </p>
          <p className="text-xs text-text-mid">of 52w band</p>
        </div>
        <div className="glass-hover rounded-xl p-3">
          <p className="text-2xs uppercase tracking-wider text-text-low">SMA-50</p>
          <p className="mt-1 font-mono text-sm font-bold tabular text-text-hi">
            {fmtCurrency(sma_50)}
          </p>
          <p className="text-xs text-text-mid">20d avg</p>
        </div>
        <div className="glass-hover rounded-xl p-3">
          <p className="text-2xs uppercase tracking-wider text-text-low">SMA-200</p>
          <p className="mt-1 font-mono text-sm font-bold tabular text-text-hi">
            {fmtCurrency(sma_200)}
          </p>
          <p className="text-xs text-text-mid">200d avg</p>
        </div>
      </div>

      <p className="mt-3 text-2xs text-text-low" data-live-snapshot>
        Position vs the real 52-week high/low —{' '}
        {pct_off_52w_high != null
          ? pct_off_52w_high === 0
            ? 'at the 52-week high'
            : `${Math.abs(pct_off_52w_high).toFixed(1)}% ${pct_off_52w_high > 0 ? 'above' : 'below'} the high`
          : 'live snapshot'}
        . Full historical bars arrive with a backtest run.
      </p>
    </div>
  )
}
