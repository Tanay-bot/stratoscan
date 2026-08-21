import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { useToasts } from '@/store/notifications'
import { cn } from '@/lib/utils'

const ICONS = {
  success: <CheckCircle2 className="h-4 w-4 text-gain-soft" aria-hidden="true" />,
  error: <XCircle className="h-4 w-4 text-loss-soft" aria-hidden="true" />,
  info: <Info className="h-4 w-4 text-neon" aria-hidden="true" />,
}

export function Toaster() {
  const { toasts, dismiss } = useToasts()

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-full max-w-sm flex-col gap-2"
    >
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 32, scale: 0.96 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            className={cn(
              'glass-strong pointer-events-auto flex items-start gap-3 rounded-xl p-4 shadow-card',
            )}
            role="status"
          >
            <span className="mt-0.5 shrink-0">{ICONS[t.kind]}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-text-hi">{t.title}</p>
              {t.message && (
                <p className="mt-0.5 text-xs leading-relaxed text-text-mid">{t.message}</p>
              )}
            </div>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 rounded-md p-1 text-text-low transition-colors hover:bg-white/10 hover:text-text-hi focus-visible:outline-2 focus-visible:outline-neon"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
