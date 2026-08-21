import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Terms } from './Terms'
import { Privacy } from './Privacy'
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

describe('legal links', () => {
  it('Footer Legal column links to real /terms and /privacy routes', () => {
    render(
      <MemoryRouter>
        <Footer />
      </MemoryRouter>,
    )
    const terms = screen.getByRole('link', { name: 'Terms of Service' })
    const privacy = screen.getByRole('link', { name: 'Privacy Policy' })
    expect(terms).toHaveAttribute('href', '/terms')
    expect(privacy).toHaveAttribute('href', '/privacy')
  })
})
