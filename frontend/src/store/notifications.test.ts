import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast, useToasts } from './notifications'

describe('notifications store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useToasts.setState({ toasts: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('pushes a toast with an id', () => {
    useToasts.getState().push('success', 'Saved', 'All good')
    const toasts = useToasts.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]).toMatchObject({ kind: 'success', title: 'Saved', message: 'All good' })
    expect(typeof toasts[0].id).toBe('number')
  })

  it('auto-dismisses after 4.2s', () => {
    useToasts.getState().push('error', 'Failed')
    expect(useToasts.getState().toasts).toHaveLength(1)
    vi.advanceTimersByTime(4300)
    expect(useToasts.getState().toasts).toHaveLength(0)
  })

  it('dismisses manually', () => {
    const id = useToasts.getState().push('info', 'Hi')
    useToasts.getState().dismiss(id)
    expect(useToasts.getState().toasts).toHaveLength(0)
  })

  it('toast helpers route to the right kind', () => {
    toast.success('S')
    toast.error('E')
    toast.info('I')
    const titles = useToasts.getState().toasts.map((t) => t.kind)
    expect(titles).toEqual(['success', 'error', 'info'])
  })
})
