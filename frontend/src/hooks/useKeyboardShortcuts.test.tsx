import { useState } from 'react'
import { describe, expect, it, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  getSearchInput,
  registerSearchInput,
  unregisterSearchInput,
  useKeyboardShortcuts,
  useRegisterDismisser,
  useRegisterSearchInput,
} from '@/hooks/useKeyboardShortcuts'
import { ShortcutHelpDialog } from '@/components/ShortcutHelpDialog'

/** Harness wiring the hook to a real search input, a form, and the help dialog. */
function Harness({ withDismisser = false }: { withDismisser?: boolean }) {
  const { helpOpen, setHelpOpen } = useKeyboardShortcuts()
  const searchRef = useRegisterSearchInput(1)
  const [dismissed, setDismissed] = useState(0)
  useRegisterDismisser(withDismisser ? () => setDismissed((d) => d + 1) : null)
  return (
    <div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          const input = e.currentTarget.querySelector('input')!
          input.dataset.submitted = input.value
        }}
      >
        <input ref={searchRef} aria-label="Screen query" data-testid="query" />
        <button type="submit">Go</button>
      </form>
      <p data-testid="dismissed">{dismissed}</p>
      <ShortcutHelpDialog open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  )
}

function fireShortcut(mod: 'meta' | 'ctrl', key: string) {
  fireEvent.keyDown(window, { key, [mod + 'Key']: true })
}

describe('search-input registry', () => {
  it('returns the highest-priority, most-recently-registered input', () => {
    const a = document.createElement('input')
    const b = document.createElement('input')
    registerSearchInput(a, 1)
    registerSearchInput(b, 0) // lower priority
    expect(getSearchInput()).toBe(a)
    registerSearchInput(b, 1) // same priority, registered later
    expect(getSearchInput()).toBe(b)
    unregisterSearchInput(b)
    expect(getSearchInput()).toBe(a)
    unregisterSearchInput(a)
    expect(getSearchInput()).toBeNull()
  })

  it('ignores null registrations', () => {
    registerSearchInput(null, 1)
    expect(getSearchInput()).toBeNull()
  })
})

describe('Cmd/Ctrl+K', () => {
  beforeEach(() => {
    // jsdom keeps focus state between renders; normalize before each case
    document.body.innerHTML = ''
  })

  it('focuses and selects the registered search input', () => {
    render(<Harness />)
    const input = screen.getByTestId('query') as HTMLInputElement
    input.value = 'hello'
    fireShortcut('meta', 'k')
    expect(document.activeElement).toBe(input)
    expect(input.selectionStart).toBe(0)
    expect(input.selectionEnd).toBe(5)
  })

  it('works with Ctrl on non-Mac', () => {
    render(<Harness />)
    const input = screen.getByTestId('query') as HTMLInputElement
    fireShortcut('ctrl', 'k')
    expect(document.activeElement).toBe(input)
  })

  it('does not hijack a plain "k" keystroke while typing', () => {
    render(<Harness />)
    const input = screen.getByTestId('query') as HTMLInputElement
    input.focus()
    fireEvent.keyDown(input, { key: 'k' })
    expect(document.activeElement).toBe(input) // still typing, not re-focused
  })
})

describe('Cmd/Ctrl+/ help dialog', () => {
  it('opens the help dialog on Cmd+/ and closes it on Esc', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireShortcut('meta', '/')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Keyboard shortcuts')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('Esc', () => {
  it('dismisses the top registered modal when one is open', () => {
    render(<Harness withDismisser />)
    const input = screen.getByTestId('query') as HTMLInputElement
    input.focus()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.getByTestId('dismissed').textContent).toBe('1')
  })

  it('blurs a focused search input when no modal is open', () => {
    render(<Harness />)
    const input = screen.getByTestId('query') as HTMLInputElement
    input.focus()
    expect(document.activeElement).toBe(input)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(document.activeElement).not.toBe(input)
  })
})

describe('Cmd/Ctrl+Enter', () => {
  it('submits the enclosing form with the input value', () => {
    render(<Harness />)
    const input = screen.getByTestId('query') as HTMLInputElement
    input.focus()
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })
    expect(input.dataset.submitted).toBe('')
    // value the form read at submit time
    input.value = 'value stocks'
    input.focus()
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true })
    expect(input.dataset.submitted).toBe('value stocks')
  })

  it('does nothing on a plain Enter in an input', () => {
    render(<Harness />)
    const input = screen.getByTestId('query') as HTMLInputElement
    input.focus()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input.dataset.submitted).toBeUndefined()
  })
})
