import { cn } from '@/lib/utils'

function toneFor(score?: number | null): 'bullish' | 'bearish' | 'neutral' {
  if (score == null) return 'neutral'
  if (score >= 0.15) return 'bullish'
  if (score <= -0.15) return 'bearish'
  return 'neutral'
}

export function SentimentBadge({
  score,
  label,
  compact,
  className,
}: {
  score?: number | null
  label?: string | null
  compact?: boolean
  className?: string
}) {
  const tone = toneFor(score ?? null)
  const text =
    label ?? (tone === 'bullish' ? 'Bullish' : tone === 'bearish' ? 'Bearish' : 'Neutral')

  const styles = {
    bullish: 'bg-gain/10 text-gain-soft border-gain/25',
    bearish: 'bg-loss/10 text-loss-soft border-loss/25',
    neutral: 'bg-white/[0.06] text-text-mid border-white/10',
  }
  const dots = {
    bullish: 'bg-gain',
    bearish: 'bg-loss',
    neutral: 'bg-white/40',
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-semibold',
        styles[tone],
        className,
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', dots[tone])} aria-hidden="true" />
      {!compact && text}
      <span className="sr-only">{text}</span>
    </span>
  )
}
