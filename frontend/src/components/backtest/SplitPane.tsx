import { useEffect, useRef, useState } from 'react'
import { GripVertical, GripHorizontal } from 'lucide-react'
import { cn } from '@/lib/utils'

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v))

/**
 * Resizable two-pane split. Ratios persist to localStorage so the user's
 * layout survives reloads. Divider is keyboard-accessible (arrow keys) and
 * pointer-draggable with `touch-none` so it works on touch screens too.
 */
export function SplitPane({
  direction,
  defaultRatio,
  minRatio = 0.2,
  maxRatio = 0.8,
  storageKey,
  className,
  paneClassName,
  first,
  second,
  labels,
}: {
  direction: 'row' | 'column'
  defaultRatio: number
  minRatio?: number
  maxRatio?: number
  storageKey: string
  className?: string
  paneClassName?: string
  first: React.ReactNode
  second: React.ReactNode
  labels?: { first: string; second: string }
}) {
  const [ratio, setRatio] = useState<number>(() => {
    if (typeof window === 'undefined') return defaultRatio
    try {
      const raw = window.localStorage.getItem(storageKey)
      if (raw) {
        const n = parseFloat(raw)
        if (Number.isFinite(n)) return clamp(n, minRatio, maxRatio)
      }
    } catch {
      /* private mode etc. */
    }
    return defaultRatio
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, String(ratio))
    } catch {
      /* ignore */
    }
  }, [ratio, storageKey])

  const containerRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ startPos: number; startRatio: number; size: number } | null>(null)

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault()
    const el = containerRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const size = direction === 'row' ? rect.width : rect.height
    const startPos = direction === 'row' ? e.clientX - rect.left : e.clientY - rect.top
    drag.current = { startPos, startRatio: ratio, size }

    const onMove = (ev: PointerEvent) => {
      if (!drag.current) return
      const rect2 = el.getBoundingClientRect()
      const pos = direction === 'row' ? ev.clientX - rect2.left : ev.clientY - rect2.top
      const delta = (pos - drag.current.startPos) / drag.current.size
      setRatio(clamp(drag.current.startRatio + delta, minRatio, maxRatio))
    }
    const onUp = () => {
      drag.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      document.body.classList.remove('select-none')
    }

    document.body.classList.add('select-none')
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  const nudge = (delta: number) => {
    setRatio((r) => clamp(r + delta, minRatio, maxRatio))
  }

  const isRow = direction === 'row'
  const pct = `${ratio * 100}%`

  return (
    <div
      ref={containerRef}
      className={cn('flex min-h-0 min-w-0', isRow ? 'flex-row' : 'flex-col', className)}
      style={isRow ? { width: '100%' } : { height: '100%' }}
    >
      <div
        className={cn('min-h-0 min-w-0', isRow ? 'h-full' : 'w-full', paneClassName)}
        style={{ flexBasis: pct, flexGrow: 0, flexShrink: 1 }}
        data-pane-ratio
      >
        {first}
      </div>

      {/* Divider
        WAI-ARIA focusable-separator pattern (role=separator + tabIndex + arrow-key
        value changes, with aria-valuenow). This IS the correct interactive widget
        for a resizable split — jsx-a11y's static analysis can't know tabIndex makes
        a separator interactive, so the two rules below false-positive on the
        intentional keyboard/pointer handlers. Block-disabled with rationale.
      */}
      {/* eslint-disable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex -- WAI-ARIA focusable separator widget */}
      <div
        role="separator"
        aria-orientation={isRow ? 'vertical' : 'horizontal'}
        aria-label={`Resize ${labels?.first ?? 'first'} and ${labels?.second ?? 'second'}`}
        aria-valuemin={Math.round(minRatio * 100)}
        aria-valuemax={Math.round(maxRatio * 100)}
        aria-valuenow={Math.round(ratio * 100)}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onKeyDown={(e) => {
          const step = e.shiftKey ? 0.05 : 0.02
          if (isRow) {
            if (e.key === 'ArrowLeft') {
              e.preventDefault()
              nudge(-step)
            }
            if (e.key === 'ArrowRight') {
              e.preventDefault()
              nudge(step)
            }
          } else {
            if (e.key === 'ArrowUp') {
              e.preventDefault()
              nudge(-step)
            }
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              nudge(step)
            }
          }
          if (e.key === 'Home') {
            e.preventDefault()
            setRatio(minRatio)
          }
          if (e.key === 'End') {
            e.preventDefault()
            setRatio(maxRatio)
          }
        }}
        className={cn(
          'group relative z-10 shrink-0 touch-none select-none focus:outline-none',
          isRow
            ? 'h-full w-2.5 cursor-col-resize focus-visible:bg-neon/10'
            : 'h-2.5 w-full cursor-row-resize focus-visible:bg-neon/10',
        )}
      >
        {/* Grip rail */}
        <span
          className={cn(
            'pointer-events-none absolute rounded-full bg-white/10 transition-all duration-200',
            'group-hover:bg-neon/70 group-focus-visible:bg-neon/70 group-hover:shadow-glow-sm',
            isRow
              ? 'left-1/2 top-1/2 h-10 w-[3px] -translate-x-1/2 -translate-y-1/2'
              : 'left-1/2 top-1/2 h-[3px] w-10 -translate-x-1/2 -translate-y-1/2',
          )}
        />
        <span
          className={cn(
            'pointer-events-none absolute text-text-low opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100',
            isRow
              ? 'left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2'
              : 'left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2',
          )}
        >
          {isRow ? (
            <GripVertical className="h-4 w-4 text-neon" />
          ) : (
            <GripHorizontal className="h-4 w-4 text-neon" />
          )}
        </span>
      </div>
      {/* eslint-enable jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-noninteractive-tabindex */}

      <div
        className={cn('min-h-0 min-w-0', isRow ? 'h-full' : 'w-full', paneClassName)}
        style={{ flex: 1 }}
      >
        {second}
      </div>
    </div>
  )
}
