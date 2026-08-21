import { useEffect, useMemo, useState } from 'react'
import { ALL_STOCKS } from '@/lib/api/demo'
import { DEMO_MODE } from '@/lib/api/client'
import { listStocks } from '@/lib/api/services'
import { fmtCurrency } from '@/lib/utils'
import { cn } from '@/lib/utils'
import { useReducedMotion } from '@/hooks/useReducedMotion'
import type { StockResult } from '@/lib/api/types'

function TickerRow({ items, doubled }: { items: StockResult[]; doubled?: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-8 pr-8" aria-hidden={doubled || undefined}>
      {items.map((s) => {
        const up = (s.price_change_pct ?? 0) >= 0
        return (
          <span key={s.symbol} className="flex items-center gap-2 whitespace-nowrap">
            <span className="font-mono text-xs font-bold tracking-wide text-text-hi">
              {s.symbol}
            </span>
            <span className="font-mono text-xs tabular text-text-mid">
              {fmtCurrency(s.current_price)}
            </span>
            <span
              className={cn(
                'font-mono text-xs font-semibold tabular',
                up ? 'text-gain-soft' : 'text-loss-soft',
              )}
            >
              {up ? '▲' : '▼'} {Math.abs(s.price_change_pct ?? 0).toFixed(2)}%
            </span>
            <span className="mx-3 h-1 w-1 rounded-full bg-white/15" aria-hidden="true" />
          </span>
        )
      })}
    </div>
  )
}

export function TickerTape({ className }: { className?: string }) {
  const [liveStocks, setLiveStocks] = useState<StockResult[]>([])
  const reduced = useReducedMotion()

  // In demo mode, use the synthetic universe. In production, fetch real
  // prices from the backend /stocks endpoint (zero LLM, zero Finnhub —
  // just a DB read, so it's cheap to hit on page load).
  useEffect(() => {
    if (DEMO_MODE) return
    let cancelled = false
    listStocks(30, 0)
      .then((res) => {
        if (!cancelled) setLiveStocks(res.results)
      })
      .catch(() => {
        /* API unreachable — tape stays empty, no error shown */
      })
    return () => { cancelled = true }
  }, [])

  const items = useMemo(() => {
    if (DEMO_MODE) return [...ALL_STOCKS].sort(() => Math.random() - 0.5)
    // Shuffle live stocks for visual variety
    return [...liveStocks].sort(() => Math.random() - 0.5)
  }, [liveStocks])

  // In production with no data yet, render nothing (avoids a flash of
  // empty space before the API responds).
  if (!DEMO_MODE && items.length === 0) return null

  return (
    <div className={cn('ticker-mask overflow-hidden', className)}>
      {/* Reduced motion: single static row, no marquee loop (the doubled
          copy exists only to make the 32s scroll seamless). */}
      <div className={cn('flex w-max', !reduced && 'animate-ticker')}>
        <TickerRow items={items} />
        {!reduced && <TickerRow items={items} doubled />}
      </div>
    </div>
  )
}
