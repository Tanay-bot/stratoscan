import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const { saveStrategy, deleteStrategy } = vi.hoisted(() => ({
  saveStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
}))
vi.mock('@/lib/api/services', () => ({ saveStrategy, deleteStrategy }))

import { FollowButton } from './FollowButton'

const renderIt = (props: Partial<Parameters<typeof FollowButton>[0]> = {}) =>
  render(
    <MemoryRouter>
      <FollowButton jobId="job-1" signedIn {...props} />
    </MemoryRouter>,
  )

beforeEach(() => {
  saveStrategy.mockReset()
  deleteStrategy.mockReset()
})

describe('FollowButton', () => {
  it('follows by job id only — the server saves the rules from its own result', async () => {
    saveStrategy.mockResolvedValue({ id: 7 })
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: /follow weekly/i }))
    expect(saveStrategy).toHaveBeenCalledWith('job-1')
    expect(await screen.findByRole('button', { name: /following · unfollow/i })).toBeInTheDocument()
  })

  it('can unfollow straight away', async () => {
    saveStrategy.mockResolvedValue({ id: 7 })
    deleteStrategy.mockResolvedValue(undefined)
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: /follow weekly/i }))
    await userEvent.click(await screen.findByRole('button', { name: /unfollow/i }))
    expect(deleteStrategy).toHaveBeenCalledWith(7)
    expect(await screen.findByRole('button', { name: /follow weekly/i })).toBeInTheDocument()
  })

  it('stays un-followed when the save is refused (e.g. the cap)', async () => {
    saveStrategy.mockRejectedValue(new Error('You can follow up to 20 strategies.'))
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: /follow weekly/i }))
    expect(screen.getByRole('button', { name: /follow weekly/i })).toBeInTheDocument()
  })

  it('is disabled until a job has finished', () => {
    renderIt({ jobId: null })
    expect(screen.getByRole('button', { name: /follow weekly/i })).toBeDisabled()
  })

  it('a signed-out trial gets the signup ask, never a dead button', () => {
    renderIt({ signedIn: false })
    expect(screen.getByRole('link', { name: /follow weekly/i })).toHaveAttribute(
      'href',
      '/signup?next=%2Fapp%2Fbacktest',
    )
    expect(saveStrategy).not.toHaveBeenCalled()
  })
})
