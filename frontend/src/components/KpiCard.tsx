import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
  tone = 'neutral',
  className,
  delay = 0,
}: {
  label: string
  value: string
  sub?: string
  icon?: LucideIcon
  tone?: 'neutral' | 'gain' | 'loss' | 'neon' | 'grape'
  className?: string
  delay?: number
}) {
  const tones = {
    neutral: 'text-text-hi',
    gain: 'text-gain-soft',
    loss: 'text-loss-soft',
    neon: 'text-neon',
    grape: 'text-grape-soft',
  }
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay, ease: [0.16, 1, 0.3, 1] }}
      className={cn('glass-hover rounded-2xl p-4', className)}
    >
      <div className="flex items-center justify-between">
        <p className="text-2xs font-semibold uppercase tracking-[0.14em] text-text-low">{label}</p>
        {Icon && <Icon className="h-4 w-4 text-text-low" aria-hidden="true" />}
      </div>
      <p className={cn('mt-2 font-mono text-2xl font-bold tabular tracking-tight', tones[tone])}>
        {value}
      </p>
      {sub && <p className="mt-1 text-xs text-text-mid">{sub}</p>}
    </motion.div>
  )
}
