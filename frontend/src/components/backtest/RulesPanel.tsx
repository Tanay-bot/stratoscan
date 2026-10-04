import { useEffect, useId, useState } from 'react'
import { RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { describeStrategy, rsiThreshold } from '@/lib/rules'

export interface RuleEdits {
  rsiBuy: number | null
  rsiSell: number | null
}

interface RulesPanelProps {
  /** `BacktestResponse.strategy` — exactly what the engine ran. */
  strategy: Record<string, unknown> | null | undefined
  /** Re-run the same query with these thresholds (no retyping). */
  onRerun: (edits: RuleEdits) => void
  disabled?: boolean
}

/**
 * Layer 3: "Show the rules". The trade rules and settings the engine
 * actually ran, in plain English, with defaults labelled as defaults — and
 * the RSI thresholds editable in place, so a rule can be changed and
 * re-run without retyping the query.
 */
export function RulesPanel({ strategy, onRerun, disabled }: RulesPanelProps) {
  const view = describeStrategy(strategy)
  const buys = Array.isArray(strategy?.buy_rules) ? (strategy?.buy_rules as string[]) : []
  const sells = Array.isArray(strategy?.sell_rules) ? (strategy?.sell_rules as string[]) : []
  const buyId = useId()
  const sellId = useId()
  const ranBuy = rsiThreshold(buys, '<')
  const ranSell = rsiThreshold(sells, '>')
  const [buy, setBuy] = useState<string>(ranBuy?.toString() ?? '')
  const [sell, setSell] = useState<string>(ranSell?.toString() ?? '')
  useEffect(() => setBuy(ranBuy?.toString() ?? ''), [ranBuy])
  useEffect(() => setSell(ranSell?.toString() ?? ''), [ranSell])

  const parse = (v: string) => (v.trim() === '' ? null : Number(v))
  const nextBuy = parse(buy)
  const nextSell = parse(sell)
  const invalid =
    [nextBuy, nextSell].some((n) => n !== null && (!Number.isFinite(n) || n <= 0 || n >= 100)) ||
    (nextBuy !== null && nextSell !== null && nextBuy >= nextSell)
  const changed = nextBuy !== ranBuy || nextSell !== ranSell

  return (
    <div className="space-y-4 text-sm">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-2xs font-semibold uppercase tracking-wider text-text-mid">Buys when</p>
          <ul className="mt-1.5 space-y-1">
            {view.entry.map((r) => (
              <li key={r} className="text-text-hi">
                {r}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-2xs font-semibold uppercase tracking-wider text-text-mid">
            Sells when
          </p>
          <ul className="mt-1.5 space-y-1">
            {view.exit.map((r) => (
              <li key={r} className="text-text-hi">
                {r}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="text-xs text-text-mid">
        All conditions in a list must hold at a day&apos;s close; orders fill at the next open.
      </p>

      <div>
        <p className="text-2xs font-semibold uppercase tracking-wider text-text-mid">Settings</p>
        <dl className="mt-1.5 grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
          {view.params.map((p) => (
            <div key={p.label} className="flex items-baseline justify-between gap-3">
              <dt className="text-text-mid">{p.label}</dt>
              <dd className="font-mono text-xs tabular text-text-hi">
                {p.value}
                {p.isDefault && (
                  <span className="ml-1.5 font-sans text-[10px] text-text-mid">default</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="rounded-xl border border-white/[0.08] p-3">
        <p className="text-xs font-semibold text-text-hi">Change the RSI rules</p>
        <p className="mt-0.5 text-xs text-text-mid">
          Leave a field blank to keep what your strategy says.
        </p>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <div>
            <label htmlFor={buyId} className="text-xs text-text-mid">
              Buy when RSI below
            </label>
            <Input
              id={buyId}
              type="number"
              inputMode="decimal"
              value={buy}
              placeholder={ranBuy === null ? 'not used' : String(ranBuy)}
              onChange={(e) => setBuy(e.target.value)}
              className="mt-1 h-9"
            />
          </div>
          <div>
            <label htmlFor={sellId} className="text-xs text-text-mid">
              Sell when RSI above
            </label>
            <Input
              id={sellId}
              type="number"
              inputMode="decimal"
              value={sell}
              placeholder={ranSell === null ? 'not used' : String(ranSell)}
              onChange={(e) => setSell(e.target.value)}
              className="mt-1 h-9"
            />
          </div>
        </div>
        {invalid && (
          <p role="alert" className="mt-2 text-xs text-loss-soft">
            Use values between 1 and 99, with the buy level below the sell level.
          </p>
        )}
        <Button
          size="sm"
          variant="outline"
          className="mt-3"
          disabled={disabled || invalid || !changed}
          onClick={() => onRerun({ rsiBuy: nextBuy, rsiSell: nextSell })}
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> Re-run with these rules
        </Button>
      </div>
    </div>
  )
}
