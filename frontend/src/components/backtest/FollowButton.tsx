import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BellPlus, BellRing } from 'lucide-react'
import { Button, buttonVariants } from '@/components/ui/button'
import { deleteStrategy, saveStrategy } from '@/lib/api/services'
import { toApiError } from '@/lib/api/client'
import { toast } from '@/store/notifications'

const SIGNUP = '/signup?next=%2Fapp%2Fbacktest'

interface FollowButtonProps {
  /** The finished async job to follow; null while none has finished. */
  jobId: string | null
  signedIn: boolean
  disabled?: boolean
}

/**
 * Day 10: follow a finished backtest. The server saves the rules and symbols
 * from its own stored result and emails the owner weekly on how it has done
 * since today. Signed-out trial users get the honest ask instead: the email
 * needs an account to go to.
 *
 * Render it with `key={jobId}` so a new run starts un-followed.
 */
export function FollowButton({ jobId, signedIn, disabled }: FollowButtonProps) {
  const [savedId, setSavedId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  if (!signedIn) {
    return (
      <Link
        to={SIGNUP}
        className={buttonVariants({ variant: 'outline', size: 'sm' })}
        title="Create a free account to get a weekly email on how this strategy does"
      >
        <BellPlus className="h-3.5 w-3.5" aria-hidden="true" /> Follow weekly
      </Link>
    )
  }

  const follow = async () => {
    if (!jobId) return
    setBusy(true)
    try {
      const s = await saveStrategy(jobId)
      setSavedId(s.id)
      toast.success(
        'Following',
        'Each week you’ll get an email on how this strategy has done since today. Turn it off in Settings → Email.',
      )
    } catch (err) {
      toast.error('Could not follow', toApiError(err).message)
    } finally {
      setBusy(false)
    }
  }

  const unfollow = async () => {
    if (savedId === null) return
    setBusy(true)
    try {
      await deleteStrategy(savedId)
      setSavedId(null)
      toast.success('Unfollowed', 'No more weekly updates for this strategy.')
    } catch (err) {
      toast.error('Could not unfollow', toApiError(err).message)
    } finally {
      setBusy(false)
    }
  }

  if (savedId !== null) {
    return (
      <Button variant="ghost" size="sm" loading={busy} onClick={() => void unfollow()}>
        {!busy && <BellRing className="h-3.5 w-3.5 text-gain-soft" aria-hidden="true" />}
        Following · Unfollow
      </Button>
    )
  }
  return (
    <Button
      variant="outline"
      size="sm"
      loading={busy}
      disabled={disabled || !jobId}
      onClick={() => void follow()}
      title="Save this strategy and get a weekly email on how it does from here"
    >
      {!busy && <BellPlus className="h-3.5 w-3.5" aria-hidden="true" />} Follow weekly
    </Button>
  )
}
