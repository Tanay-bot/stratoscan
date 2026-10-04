import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BellOff, BellRing, Play, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { Skeleton } from '@/components/ui/skeleton'
import { deleteStrategy, listStrategies } from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import type { SavedStrategy } from '@/lib/api/types'
import { toast } from '@/store/notifications'
import { fmtPct, timeAgo } from '@/lib/utils'

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; strategies: SavedStrategy[] }

/** Re-run a saved strategy: the Backtest page reads `q` from its setup link. */
export const rerunHref = (s: SavedStrategy) => `/app/backtest?q=${encodeURIComponent(s.query)}`

/**
 * Day 10: the strategies this account follows. Each one is re-run weekly
 * from the day it was saved and emailed to the owner; this is where they see
 * what they follow, run one again, or stop following it.
 */
export function SavedStrategies() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', strategies: await listStrategies() })
    } catch (err) {
      setState({ kind: 'error', message: toApiError(err).message })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (state.kind === 'loading') {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading your saved strategies">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <EmptyState
        icon={BellRing}
        title="Couldn't load your saved strategies"
        description={state.message}
        action={
          <Button variant="outline" size="sm" onClick={() => void load()}>
            <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> Try again
          </Button>
        }
      />
    )
  }
  if (state.strategies.length === 0) {
    return (
      <EmptyState
        icon={BellRing}
        title="No saved strategies yet"
        description="Press “Follow weekly” on a finished backtest. Each week you’ll get one email on how it has done since you saved it — good or bad — next to SPY over the same days."
        action={
          <Link to="/app/backtest" className="text-sm font-medium text-neon hover:underline">
            Run a backtest to follow
          </Link>
        }
      />
    )
  }

  const unfollow = async (s: SavedStrategy) => {
    try {
      await deleteStrategy(s.id)
      setState({ kind: 'ready', strategies: state.strategies.filter((x) => x.id !== s.id) })
      toast.success('Unfollowed', 'No more weekly updates for this strategy.')
    } catch (err) {
      toast.error('Could not unfollow', toApiError(err).message)
    }
  }

  return (
    <ul className="space-y-2">
      {state.strategies.map((s) => (
        <li key={s.id} className="glass-hover flex items-center gap-3 rounded-xl p-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-text-hi">“{s.query}”</p>
            <p className="truncate text-2xs text-text-low">
              {s.saved_return_pct != null ? `${fmtPct(s.saved_return_pct, 1)} when saved · ` : ''}
              {s.symbols.length > 0 ? `${s.symbols.slice(0, 5).join(', ')} · ` : ''}
              saved {timeAgo(s.saved_at)}
            </p>
          </div>
          <Link
            to={rerunHref(s)}
            aria-label={`Run ${s.query} again`}
            className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-neon"
          >
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
          <button
            onClick={() => void unfollow(s)}
            aria-label={`Stop following ${s.query}`}
            className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-loss-soft"
          >
            <BellOff className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </li>
      ))}
    </ul>
  )
}
