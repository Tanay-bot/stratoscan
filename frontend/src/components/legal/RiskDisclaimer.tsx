import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The risk disclaimer — shown NEXT TO the numbers, not in a footer.
 *
 * A disclaimer nobody reads is a disclaimer that does not exist, and grey 8pt
 * text at the bottom of a page is where honesty goes to be ignored. This one is
 * the same visual weight as the KPI cards it sits under and links to the full
 * explanation, so the promise a backtest makes is legible at the moment the
 * number is read.
 *
 * Reusable on purpose: the Day 6 share page renders results a stranger can
 * open, where the disclaimer matters more than it does for the author, and it
 * must not be reworded in a second place.
 */
export function RiskDisclaimer({ className }: { className?: string }) {
  return (
    <div
      role="note"
      aria-label="Risk disclaimer"
      className={cn(
        'flex items-start gap-3 rounded-2xl border border-amber-300/20 bg-amber-300/[0.06] px-4 py-3',
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
      <p className="text-xs leading-relaxed text-text-mid">
        Simulated results on historical data, including fees and slippage. Past performance does not
        predict future results. This is not investment advice.{' '}
        <Link
          to="/risk"
          className="whitespace-nowrap text-neon transition-opacity hover:opacity-80"
        >
          Read the risk disclaimer
        </Link>
      </p>
    </div>
  )
}
