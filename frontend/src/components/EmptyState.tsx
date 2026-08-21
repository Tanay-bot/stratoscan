import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-white/10 px-6 py-14 text-center',
        className,
      )}
    >
      {Icon && (
        <div className="mb-2 flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.05]">
          <Icon className="h-6 w-6 text-text-low" aria-hidden="true" />
        </div>
      )}
      <h3 className="text-base font-semibold text-text-hi">{title}</h3>
      {description && (
        <p className="max-w-sm text-sm leading-relaxed text-text-mid">{description}</p>
      )}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
