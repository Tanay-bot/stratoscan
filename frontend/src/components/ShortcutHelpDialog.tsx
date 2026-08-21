import { Command } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ['K'], label: 'Focus search' },
  { keys: ['/'], label: 'Toggle shortcut help' },
  { keys: ['Enter'], label: 'Submit search / backtest' },
  { keys: ['Esc'], label: 'Close modal / clear search' },
]

/** Platform-aware modifier label: ⌘ on macOS, Ctrl elsewhere. */
export function modLabel() {
  return /Mac|iP(hone|ad|od)/.test(navigator.platform ?? '') ? '⌘' : 'Ctrl'
}

export function ShortcutHelpDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const mod = modLabel()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Terminal-flavored, keyboard-first.</DialogDescription>
        </DialogHeader>

        <ul className="space-y-3">
          {SHORTCUTS.map((s) => (
            <li key={s.label} className="flex items-center justify-between gap-4">
              <span className="text-sm text-text-mid">{s.label}</span>
              <span className="flex items-center gap-1 font-mono text-xs text-text-hi">
                {mod && <kbd className="shortcut-key">{mod}</kbd>}
                {s.keys.map((k) => (
                  <kbd key={k} className="shortcut-key">
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>

        <p className="mt-4 flex items-center gap-1.5 border-t border-white/[0.08] pt-3 text-2xs text-text-low">
          <Command className="h-3 w-3" aria-hidden="true" /> Works on every page. Close with Esc.
        </p>
      </DialogContent>
    </Dialog>
  )
}
