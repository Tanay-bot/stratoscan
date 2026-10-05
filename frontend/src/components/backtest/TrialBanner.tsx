import { Link } from 'react-router-dom'
import { Lock, Sparkles } from 'lucide-react'
import { buttonVariants } from '@/components/ui/button'

/** Free backtests a trial session gets (mirrors ANON_BACKTEST_ALLOWANCE). */
export const TRIAL_ALLOWANCE = 3

interface TrialBannerProps {
  /** Free runs left, when the server has said; null before the first run. */
  left: number | null
  /** The server refused a run because the allowance is spent. */
  exhausted: boolean
}

const SIGNUP = '/signup?next=%2Fapp%2Fbacktest'
const LOGIN = '/login?next=%2Fapp%2Fbacktest'

/**
 * The strip a signed-out visitor sees above the backtest workspace.
 *
 * "After value, never before": it never blocks a run. It counts down the
 * free allowance and, only once the server refuses a run, turns into the
 * ask — with the promise that matters, that nothing made so far is lost.
 */
export function TrialBanner({ left, exhausted }: TrialBannerProps) {
  if (exhausted) {
    return (
      <div
        role="status"
        className="mb-3 flex shrink-0 flex-col gap-3 rounded-2xl border border-neon/30 bg-neon/[0.06] p-4 sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="flex items-start gap-3">
          <Lock className="mt-0.5 h-4 w-4 shrink-0 text-neon" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-text-hi">
              You&apos;ve used your {TRIAL_ALLOWANCE} free backtests.
            </p>
            <p className="text-xs text-text-mid">
              Create a free account to keep going — your results and share links come with you. No
              card needed.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Link to={SIGNUP} className={buttonVariants({ size: 'sm' })}>
            Create free account
          </Link>
          <Link to={LOGIN} className={buttonVariants({ size: 'sm', variant: 'outline' })}>
            Log in
          </Link>
        </div>
      </div>
    )
  }
  const remaining = left ?? TRIAL_ALLOWANCE
  return (
    <div
      role="status"
      className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-xl border border-white/[0.07] bg-white/[0.03] px-3 py-2 text-xs text-text-mid"
    >
      <span className="flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-neon" aria-hidden="true" />
        Free trial, no signup —{' '}
        <strong className="font-semibold text-text-hi">
          {remaining} backtest{remaining === 1 ? '' : 's'} left
        </strong>
      </span>
      <Link to={SIGNUP} className="font-medium text-neon hover:underline">
        Save your work with a free account
      </Link>
    </div>
  )
}
