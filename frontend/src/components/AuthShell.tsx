import type { ReactNode } from 'react'
import { Logo } from '@/components/Logo'

/** Centered glass card used by the standalone account pages (reset, forgot). */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div
      id="main-content"
      tabIndex={-1}
      className="relative flex min-h-screen items-center justify-center px-5 outline-none"
    >
      <div
        className="pointer-events-none absolute -top-24 left-1/2 h-72 w-[480px] -translate-x-1/2 rounded-full bg-neon/[0.09] blur-[90px]"
        aria-hidden="true"
      />
      <div className="relative w-full max-w-md">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>
        <div className="glass-strong rounded-3xl p-8 shadow-card">{children}</div>
      </div>
    </div>
  )
}

export const fieldLabel = 'text-2xs font-semibold uppercase tracking-wider text-text-low'
