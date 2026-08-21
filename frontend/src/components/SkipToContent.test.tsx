import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SkipToContent } from './SkipToContent'

// jsdom never executes Tailwind CSS, so "visually hidden" is asserted
// structurally: the link starts off-screen (-top-[9999px]) and the focus
// variant reveals it (focus:top-4). The behavioral parts (first in tab
// order, hash navigation) are exercised for real.
describe('SkipToContent', () => {
  it('is a link pointing at the #main-content target', () => {
    render(<SkipToContent />)
    const link = screen.getByRole('link', { name: 'Skip to content' })
    expect(link).toHaveAttribute('href', '#main-content')
  })

  it('is visually hidden until focused, then revealed', () => {
    render(<SkipToContent />)
    const link = screen.getByRole('link', { name: 'Skip to content' })
    // Off-screen by default…
    expect(link.className).toContain('-top-[9999px]')
    // …and brought into view only while focused.
    expect(link.className).toContain('focus:top-4')
  })

  it('is the FIRST focusable element in tab order', async () => {
    const user = userEvent.setup()
    render(
      <>
        <SkipToContent />
        <nav>
          <a href="/features">Features</a>
          <a href="/pricing">Pricing</a>
        </nav>
        <main id="main-content" tabIndex={-1}>
          Main content
        </main>
      </>,
    )
    // Tab order: the skip link must come before any nav link. All anchors
    // have tabIndex 0, so user-event's tab() walks them in DOM order — the
    // first focus event must land on the skip link.
    const order: string[] = []
    const links = Array.from(document.querySelectorAll('a'))
    for (const link of links) {
      link.addEventListener('focus', () => order.push(link.textContent ?? ''))
    }
    for (let i = 0; i < links.length; i++) await user.tab()
    expect(order[0]).toBe('Skip to content')
  })

  it('navigates to the #main-content hash when activated', async () => {
    const user = userEvent.setup()
    render(
      <>
        <SkipToContent />
        <main id="main-content" tabIndex={-1}>
          Main content
        </main>
      </>,
    )
    const link = screen.getByRole('link', { name: 'Skip to content' })
    await user.click(link)
    // The anchor navigates to the hash. Focus transfer onto the tabIndex=-1
    // target is a browser behavior jsdom doesn't implement — it's covered by
    // the page-level test asserting the target is programmatically focusable
    // (pages/mainContent.test.tsx).
    expect(window.location.hash).toBe('#main-content')
  })
})
