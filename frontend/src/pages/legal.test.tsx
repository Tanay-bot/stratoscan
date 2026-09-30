import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Terms } from './Terms'
import { Privacy } from './Privacy'
import { Risk } from './Risk'
import { Footer } from '@/components/landing/Footer'

// P2-4: legal pages. Terms must cover the standard clauses (acceptance,
// obligations, IP, disclaimers, limitation of liability, termination,
// governing law); Privacy must cover collection, usage, storage, sharing,
// rights, cookies, and contact. Both must expose the #main-content target
// (skip-to-content) and the Footer's Legal column must link to real routes.
describe('Terms of Service', () => {
  it('renders the title and last-updated stamp', () => {
    render(
      <MemoryRouter>
        <Terms />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Terms of Service' })).toBeInTheDocument()
    expect(screen.getByText(/last updated:/i)).toBeInTheDocument()
  })

  it('covers every standard clause', () => {
    render(
      <MemoryRouter>
        <Terms />
      </MemoryRouter>,
    )
    for (const heading of [
      'Acceptance of Terms',
      'User Obligations',
      'Intellectual Property',
      'Not Financial Advice',
      'Disclaimers',
      'Limitation of Liability',
      'Termination',
      'Governing Law',
      'Contact',
    ]) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
  })

  it('exposes a focusable #main-content target and a TOC', () => {
    render(
      <MemoryRouter>
        <Terms />
      </MemoryRouter>,
    )
    const target = document.getElementById('main-content')
    expect(target).not.toBeNull()
    expect(target?.getAttribute('tabindex')).toBe('-1')
    expect(screen.getByRole('navigation', { name: 'On this page' })).toBeInTheDocument()
  })
})

describe('Privacy Policy', () => {
  it('renders the title and last-updated stamp', () => {
    render(
      <MemoryRouter>
        <Privacy />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Privacy Policy' })).toBeInTheDocument()
    expect(screen.getByText(/last updated:/i)).toBeInTheDocument()
  })

  it('covers every standard clause', () => {
    render(
      <MemoryRouter>
        <Privacy />
      </MemoryRouter>,
    )
    for (const heading of [
      'Data We Collect',
      // Day 4: the section that has to be TRUE of what the product actually
      // stores — account, saved content, anonymous events, scrubbed errors —
      // and that names the one local identifier plainly.
      'What We Store and How to Delete It',
      'How We Use Your Data',
      'Data Storage & Security',
      'Data Sharing',
      'Your Rights',
      'Cookies & Local Storage',
      'Data Retention',
      'Children’s Privacy',
      'Changes to This Policy',
      'Contact Information',
    ]) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
  })

  it('describes what is stored and the one way to delete it', () => {
    render(
      <MemoryRouter>
        <Privacy />
      </MemoryRouter>,
    )

    // The local identifier, named rather than gestured at.
    expect(screen.getAllByText(/stratoscan_anon_id/).length).toBeGreaterThan(0)
    // The deletion path is the Settings page, and the section has to say what
    // that action does — including that events are detached, not deleted.
    expect(screen.getAllByText(/Settings → Delete account/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/revokes every session/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/no longer be tied to a person/i).length).toBeGreaterThan(0)
    // And the claim that makes Parts A and B survivable: no query text is
    // stored as an event.
    expect(screen.getAllByText(/never/).length).toBeGreaterThan(0)
  })

  it('states the no-sale commitment explicitly', () => {
    render(
      <MemoryRouter>
        <Privacy />
      </MemoryRouter>,
    )
    // The <strong> around "not" splits the visible text into element
    // children, so match on each node's recursive textContent instead of
    // the flattened direct-text content.
    const matches = screen.getAllByText((_, node) => {
      if (!node || node.textContent === null) return false
      return /do not sell your personal data/i.test(node.textContent.replace(/\s+/g, ' ').trim())
    })
    expect(matches.length).toBeGreaterThan(0)
  })
})

describe('Risk Disclaimer', () => {
  it('renders the title and last-updated stamp', () => {
    render(
      <MemoryRouter>
        <Risk />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Risk Disclaimer' })).toBeInTheDocument()
    expect(screen.getByText(/last updated:/i)).toBeInTheDocument()
  })

  it('covers what a backtest is and is not', () => {
    render(
      <MemoryRouter>
        <Risk />
      </MemoryRouter>,
    )
    for (const heading of [
      'What a Backtest Actually Is',
      // The two structural biases that make a backtest look better than it is.
      'Survivorship Bias',
      'Look-Ahead and Data Limits',
      // Data honesty, then the two claims that keep this a research tool.
      'The Data Can Be Wrong or Stale',
      'Not Investment Advice',
      'Your Decisions Are Yours',
    ]) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
  })

  it('exposes the shared legal shell (skip-to-content target + TOC)', () => {
    render(
      <MemoryRouter>
        <Risk />
      </MemoryRouter>,
    )
    expect(document.getElementById('main-content')?.getAttribute('tabindex')).toBe('-1')
    expect(screen.getByRole('navigation', { name: 'On this page' })).toBeInTheDocument()
  })
})

describe('legal links', () => {
  it('Footer Legal column links to real /terms, /privacy and /risk routes', () => {
    render(
      <MemoryRouter>
        <Footer />
      </MemoryRouter>,
    )
    const terms = screen.getByRole('link', { name: 'Terms of Service' })
    const privacy = screen.getByRole('link', { name: 'Privacy Policy' })
    const risk = screen.getByRole('link', { name: 'Risk Disclaimer' })
    expect(terms).toHaveAttribute('href', '/terms')
    expect(privacy).toHaveAttribute('href', '/privacy')
    expect(risk).toHaveAttribute('href', '/risk')
  })
})
