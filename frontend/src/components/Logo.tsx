import { cn } from '@/lib/utils'

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" className={cn('h-8 w-8', className)} aria-hidden="true">
      <circle cx="16" cy="16" r="13" stroke="rgba(0,212,255,0.35)" strokeWidth="1.5" />
      <circle
        cx="16"
        cy="16"
        r="8"
        stroke="rgba(124,58,237,0.5)"
        strokeWidth="1.5"
        strokeDasharray="3 3"
      />
      <circle cx="16" cy="16" r="2.5" fill="#00d4ff" />
      <circle cx="26" cy="10" r="2" fill="#a78bfa" />
      <path
        d="M4 16c3-4 6-4 9-1s6 3 9 1 4-3 6-4"
        stroke="#00d4ff"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function Logo({ className, compact }: { className?: string; compact?: boolean }) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <LogoMark />
      {!compact && (
        <span className="text-lg font-bold tracking-tight text-text-hi">
          Stratoscan
          <span className="text-neon">.</span>
        </span>
      )}
    </span>
  )
}
