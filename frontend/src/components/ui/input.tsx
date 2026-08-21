import * as React from 'react'
import { cn } from '@/lib/utils'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  icon?: React.ReactNode
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, icon, ...props }, ref) => (
    <div className="relative w-full">
      {icon && (
        <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-text-low [&_svg]:h-4 [&_svg]:w-4">
          {icon}
        </span>
      )}
      <input
        type={type}
        ref={ref}
        className={cn(
          'flex h-11 w-full rounded-xl border border-white/10 bg-white/[0.04] px-3.5 text-sm text-text-hi',
          'placeholder:text-text-low transition-all duration-200',
          'hover:border-white/20 focus:border-neon/60 focus:bg-white/[0.06] focus:outline-none focus:ring-2 focus:ring-neon/25',
          'disabled:pointer-events-none disabled:opacity-50',
          icon && 'pl-10',
          className,
        )}
        {...props}
      />
    </div>
  ),
)
Input.displayName = 'Input'

export { Input }
