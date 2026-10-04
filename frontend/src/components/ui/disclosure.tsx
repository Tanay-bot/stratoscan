import { useId } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

interface DisclosureProps {
  title: string
  /** One line under the title saying what is inside (shown when closed too). */
  hint?: string
  open: boolean
  onToggle: () => void
  /** Layer number, shown as a small step marker. */
  step?: number
  children: React.ReactNode
  className?: string
}

/**
 * A collapsible layer: a real <button> with aria-expanded / aria-controls,
 * so it is reachable with Tab and toggled with Enter or Space, and announced
 * correctly by screen readers. Closed content is not rendered at all.
 */
export function Disclosure({
  title,
  hint,
  open,
  onToggle,
  step,
  children,
  className,
}: DisclosureProps) {
  const id = useId()
  return (
    <section className={cn('glass rounded-2xl', className)}>
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={onToggle}
          className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left transition-colors hover:bg-white/[0.03] focus-visible:outline focus-visible:outline-2 focus-visible:outline-neon"
        >
          {step !== undefined && (
            <span
              aria-hidden="true"
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-white/15 font-mono text-[10px] text-text-mid"
            >
              {step}
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-text-hi">{title}</span>
            {hint && <span className="block text-xs text-text-mid">{hint}</span>}
          </span>
          <ChevronDown
            aria-hidden="true"
            className={cn(
              'h-4 w-4 shrink-0 text-text-mid transition-transform',
              open && 'rotate-180',
            )}
          />
        </button>
      </h3>
      {open && (
        <div id={id} className="border-t border-white/[0.06] px-4 pb-4 pt-3">
          {children}
        </div>
      )}
    </section>
  )
}
