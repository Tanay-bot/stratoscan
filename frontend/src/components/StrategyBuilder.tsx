import { Plus, X } from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

export interface StrategyCondition {
  id: number
  indicator: string
  operator: string
  value: number
}

export const INDICATORS = [
  { key: 'rsi_14', label: 'RSI (14)' },
  { key: 'sma_50', label: 'SMA (50)' },
  { key: 'sma_200', label: 'SMA (200)' },
  { key: 'macd', label: 'MACD' },
  { key: 'atr', label: 'ATR (14)' },
  { key: 'bollinger', label: 'Bollinger' },
  { key: 'volume', label: 'Volume' },
  { key: 'pe', label: 'P/E ratio' },
]

const OPERATORS = [
  { key: '<', label: 'below' },
  { key: '>', label: 'above' },
  { key: '<=', label: 'at most' },
  { key: '>=', label: 'at least' },
  { key: 'cross_up', label: 'crosses above' },
  { key: 'cross_down', label: 'crosses below' },
]

let nextId = 100

export function strategyToSentence(conditions: StrategyCondition[]): string {
  if (!conditions.length)
    return 'No conditions yet — the engine will use the default momentum strategy.'
  const parts = conditions.map((c) => {
    const ind = INDICATORS.find((i) => i.key === c.indicator)?.label ?? c.indicator
    const op = OPERATORS.find((o) => o.key === c.operator)?.label ?? c.operator
    return `${ind} ${op} ${c.value}`
  })
  return `When ${parts.join(' AND ')}`
}

export function StrategyBuilder({
  conditions,
  onChange,
}: {
  conditions: StrategyCondition[]
  onChange: (next: StrategyCondition[]) => void
}) {
  const add = () => {
    const next: StrategyCondition = { id: nextId++, indicator: 'rsi_14', operator: '<', value: 30 }
    onChange([...conditions, next])
  }

  const update = (id: number, patch: Partial<StrategyCondition>) => {
    onChange(conditions.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  }

  const remove = (id: number) => {
    onChange(conditions.filter((c) => c.id !== id))
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <p className="text-2xs font-semibold uppercase tracking-wider text-text-low">
          Strategy builder
        </p>
        <Button variant="outline" size="sm" onClick={add}>
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add condition
        </Button>
      </div>

      <div className="mt-3 space-y-2.5">
        {conditions.length === 0 && (
          <p className="rounded-xl border border-dashed border-white/10 px-4 py-5 text-center text-xs text-text-low">
            Add entry conditions. Example: <span className="text-neon">When RSI is &lt; 30</span>
          </p>
        )}
        {conditions.map((c) => (
          <div key={c.id} className="glass flex flex-wrap items-center gap-2 rounded-xl p-2.5">
            <span className="pl-1 text-xs text-text-low">When</span>
            <Select value={c.indicator} onValueChange={(v) => update(c.id, { indicator: v })}>
              <SelectTrigger className="h-8 w-[118px] text-xs" aria-label="Indicator">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {INDICATORS.map((i) => (
                  <SelectItem key={i.key} value={i.key}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={c.operator} onValueChange={(v) => update(c.id, { operator: v })}>
              <SelectTrigger className="h-8 w-[104px] text-xs" aria-label="Operator">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OPERATORS.map((o) => (
                  <SelectItem key={o.key} value={o.key}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <input
              type="number"
              value={c.value}
              onChange={(e) => update(c.id, { value: Number(e.target.value) })}
              aria-label="Threshold value"
              className="h-8 w-16 rounded-lg border border-white/10 bg-white/[0.04] px-2 text-center font-mono text-xs text-text-hi focus:border-neon/60 focus:outline-none focus:ring-2 focus:ring-neon/25"
            />
            <button
              onClick={() => remove(c.id)}
              aria-label={`Remove ${c.indicator} condition`}
              className="ml-auto rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-loss-soft"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>

      {conditions.length > 0 && (
        <div className="mt-3">
          <Badge variant="grape" className="text-[11px] normal-case tracking-normal">
            {strategyToSentence(conditions)}
          </Badge>
        </div>
      )}
    </div>
  )
}
