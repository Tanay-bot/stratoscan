import { Link, Navigate, useLocation } from 'react-router-dom'
import { Logo } from '@/components/Logo'
import { buttonVariants } from '@/components/ui/button'
import { useAuth } from '@/store/auth'
import { Backtest } from './Backtest'

/**
 * `/try` — the backtester for people who have not signed up (Day 6).
 *
 * Every wall before the moment of value costs roughly half the funnel, so the
 * front door is the product itself: the same Backtest workspace, a thin
 * header instead of the app shell, and three free runs (the trial banner
 * inside Backtest counts them down). `?q=` is honored, which is how a shared
 * result's "Run this strategy yourself" lands here.
 *
 * A signed-in visitor is sent to the full app with the same query string.
 */
export function Try() {
  const token = useAuth((s) => s.token)
  const location = useLocation()
  if (token) return <Navigate to={`/app/backtest${location.search}`} replace />

  const next = encodeURIComponent('/app/backtest')
  return (
    <div className="min-h-screen bg-ink">
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-white/[0.06] bg-ink/80 px-4 backdrop-blur-xl sm:px-6">
        <Link to="/" aria-label="Stratoscan home">
          <Logo />
        </Link>
        <nav aria-label="Account" className="flex items-center gap-2">
          <Link
            to={`/login?next=${next}`}
            className={buttonVariants({ size: 'sm', variant: 'ghost' })}
          >
            Log in
          </Link>
          <Link to={`/signup?next=${next}`} className={buttonVariants({ size: 'sm' })}>
            Sign up free
          </Link>
        </nav>
      </header>
      <main id="main-content" tabIndex={-1} className="px-4 py-6 outline-none sm:px-6 lg:px-8">
        <Backtest />
      </main>
    </div>
  )
}
