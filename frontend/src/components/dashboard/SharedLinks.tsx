import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Copy, ExternalLink, Link2, Link2Off, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { Skeleton } from '@/components/ui/skeleton'
import { listShares, revokeShare } from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import type { ShareListItem } from '@/lib/api/types'
import { toast } from '@/store/notifications'
import { fmtPct, timeAgo } from '@/lib/utils'

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; shares: ShareListItem[] }

/**
 * The owner's live public links (Day 6 sharing, Day 8 empty states).
 *
 * Sharing publishes a result to anyone with the link, so the owner needs one
 * place that lists everything they have made public and turns any of it off.
 * Every state says what is going on and what to do next.
 */
export function SharedLinks() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', shares: await listShares() })
    } catch (err) {
      setState({ kind: 'error', message: toApiError(err).message })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (state.kind === 'loading') {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading your share links">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <EmptyState
        icon={Link2}
        title="Couldn't load your share links"
        description={state.message}
        action={
          <Button variant="outline" size="sm" onClick={() => void load()}>
            <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> Try again
          </Button>
        }
      />
    )
  }
  if (state.shares.length === 0) {
    return (
      <EmptyState
        icon={Link2}
        title="Nothing shared yet"
        description="Press “Share result” on any finished backtest to get a public link with its chart. Nothing is public until you do, and you can stop sharing anytime."
        action={
          <Link to="/app/backtest" className="text-sm font-medium text-neon hover:underline">
            Run a backtest to share
          </Link>
        }
      />
    )
  }

  const stop = async (share: ShareListItem) => {
    try {
      await revokeShare(share.share_id)
      setState({
        kind: 'ready',
        shares: state.shares.filter((s) => s.share_id !== share.share_id),
      })
      toast.success('Sharing stopped', 'That link no longer works for anyone.')
    } catch (err) {
      toast.error('Could not stop sharing', toApiError(err).message)
    }
  }

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url)
      toast.success('Link copied')
    } catch {
      window.prompt('Copy your share link:', url)
    }
  }

  return (
    <ul className="space-y-2">
      {state.shares.map((s) => {
        const ret = s.headline?.total_return_pct
        return (
          <li key={s.share_id} className="glass-hover flex items-center gap-3 rounded-xl p-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-text-hi">“{s.query}”</p>
              <p className="text-2xs text-text-low">
                {ret != null ? `${fmtPct(ret, 1)} · ` : ''}shared {timeAgo(s.created_at)}
              </p>
            </div>
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Open the public page for ${s.query}`}
              className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-neon"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
            <button
              onClick={() => void copy(s.url)}
              aria-label={`Copy the link for ${s.query}`}
              className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-neon"
            >
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              onClick={() => void stop(s)}
              aria-label={`Stop sharing ${s.query}`}
              className="rounded-lg p-1.5 text-text-low transition-colors hover:bg-white/10 hover:text-loss-soft"
            >
              <Link2Off className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
