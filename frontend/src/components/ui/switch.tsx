import * as React from 'react'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import { cn } from '@/lib/utils'

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    className={cn(
      'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-white/10 transition-colors',
      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-neon',
      'disabled:cursor-not-allowed disabled:opacity-50',
      'data-[state=checked]:border-neon/60 data-[state=checked]:bg-neon/25',
      'data-[state=unchecked]:bg-white/[0.07]',
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        'pointer-events-none block h-4 w-4 rounded-full bg-white/70 shadow transition-transform',
        'data-[state=checked]:translate-x-4 data-[state=checked]:bg-neon data-[state=checked]:shadow-glow-sm',
        'data-[state=unchecked]:translate-x-0.5',
      )}
    />
  </SwitchPrimitive.Root>
))
Switch.displayName = 'Switch'

export { Switch }
