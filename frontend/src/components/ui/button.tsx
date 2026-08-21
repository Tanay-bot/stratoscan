import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-semibold',
    'transition-all duration-200 select-none',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neon',
    'disabled:pointer-events-none disabled:opacity-50',
    '[&_svg]:pointer-events-none [&_svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-neon text-ink-deep shadow-glow',
          'hover:bg-[#33ddff] hover:shadow-glow hover:-translate-y-px',
          'active:translate-y-0',
        ],
        grape: [
          'bg-grape text-white shadow-glow-grape',
          'hover:bg-[#8b5cf6] hover:-translate-y-px',
          'active:translate-y-0',
        ],
        secondary: ['glass', 'text-text-hi hover:border-white/20 hover:bg-white/[0.07]'],
        ghost: ['text-text-mid hover:text-text-hi hover:bg-white/[0.06]'],
        outline: [
          'border border-white/15 bg-transparent text-text-hi',
          'hover:border-neon/50 hover:text-neon',
        ],
        danger: ['bg-loss/15 text-loss-soft hover:bg-loss/25 border border-loss/30'],
      },
      size: {
        default: 'h-10 px-5 py-2',
        sm: 'h-8 px-3 text-xs',
        lg: 'h-12 px-7 text-base',
        icon: 'h-10 w-10',
        'icon-sm': 'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'primary', size: 'default' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  loading?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size, className }))}
      disabled={disabled || loading}
      {...props}
    >
      {loading && (
        <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-90"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z"
          />
        </svg>
      )}
      {children}
    </button>
  ),
)
Button.displayName = 'Button'

export { Button, buttonVariants }
