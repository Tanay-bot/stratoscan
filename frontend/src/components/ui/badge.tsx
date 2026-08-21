import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-2xs font-semibold uppercase tracking-wider transition-colors',
  {
    variants: {
      variant: {
        default: 'bg-white/[0.07] text-text-mid',
        neon: 'bg-neon/10 text-neon border border-neon/25',
        grape: 'bg-grape/15 text-grape-soft border border-grape/30',
        gain: 'bg-gain/10 text-gain-soft border border-gain/25',
        loss: 'bg-loss/10 text-loss-soft border border-loss/25',
        neutral: 'bg-white/[0.06] text-text-mid border border-white/10',
        warning: 'bg-amber-400/10 text-amber-300 border border-amber-400/25',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {
  dot?: boolean
}

function Badge({ className, variant, dot, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />}
      {children}
    </span>
  )
}

export { Badge, badgeVariants }
