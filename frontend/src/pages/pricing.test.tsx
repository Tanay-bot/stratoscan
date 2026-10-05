import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// Settings' plan card reads the live entitlements; give it a Free account.
vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, DEMO_MODE: false }
})
vi.mock('@/lib/api/services', () => ({
  getEntitlements: vi.fn(async () => ({
    plan: 'free',
    plan_label: 'Free',
    source: 'manual',
    grandfathered: false,
    limits: {
      backtests_per_month: 50,
      saved_strategies: 3,
      csv_export: false,
      monitoring_emails: false,
    },
    usage: { backtests: 4, saved_strategies: 1 },
    pending_plan: null,
    change_at: null,
    in_grace: false,
    period: '2026-10',
    resets_at: '2026-11-01T00:00:00+00:00',
  })),
  getEmailPreferences: vi.fn(async () => ({ weekly_digest: true, email_verified: true })),
  setWeeklyDigest: vi.fn(),
  changePassword: vi.fn(),
  deleteAccount: vi.fn(),
  logoutEverywhere: vi.fn(),
  // The landing hero's live ticker — never resolves, so nothing renders from it.
  listStocks: vi.fn(() => new Promise(() => {})),
}))

import { Pricing } from './Pricing'
import { Landing } from './Landing'
import { Features } from './Features'
import { About } from './About'
import { Settings } from './Settings'
import { useAuth } from '@/store/auth'

/**
 * What a plan price looks like: an amount per period ("$9 / month", "€90 per
 * year", "$0 / forever") or a bare zero price ("$0"). Stock prices on charts
 * ("$145") and market caps ("< $2B") are data, not plan prices, and pass.
 */
const PLAN_PRICE =
  /[$€£]\s?\d[\d,]*(?:\.\d+)?\s*(?:\/|per)\s*(?:mo\b|month|yr\b|year|forever|user|seat)|[$€£]\s?0(?![\d.,])|\b\d+(?:\.\d+)?\s?(?:USD|EUR|GBP)\b/i
/** The configured price (stubbed below): it must not render while the flag is off. */
const SENTINEL = '$9 / month'

const PAGES = { Pricing, Landing, Features, About, Settings }

function textOf(Page: () => JSX.Element): Promise<string> {
  const { container } = render(
    <MemoryRouter>
      <Page />
    </MemoryRouter>,
  )
  // Let async sections (the plan card) settle before reading.
  return new Promise((resolve) => setTimeout(() => resolve(container.textContent ?? ''), 20))
}

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('price flag (Day 12): no price renders anywhere while it is off', () => {
  useAuth.setState({
    token: 'tok',
    user: { id: 1, email: 'me@x.test', plan: 'free', email_verified: true, is_admin: false },
  })

  for (const [name, Page] of Object.entries(PAGES)) {
    it(`${name} shows no price`, async () => {
      vi.stubEnv('VITE_PRICING_ENABLED', 'false')
      vi.stubEnv('VITE_PRO_PRICE', SENTINEL)
      const text = await textOf(Page)
      expect(text.length).toBeGreaterThan(50)
      expect(text).not.toContain(SENTINEL)
      expect(text).not.toMatch(PLAN_PRICE)
    })
  }

  it('Settings shows the real plan: usage, limits, what Pro adds', async () => {
    const text = await textOf(Settings)
    expect(text).toContain('Free plan')
    expect(text).toContain('4 of 50')
    expect(text).toContain('1 of 3')
    expect(text).not.toMatch(/no limits/i)
  })

  it('the pattern is not vacuous: data passes, plan prices fail', () => {
    expect('market cap < $2B and $200B+, close $145.20').not.toMatch(PLAN_PRICE)
    for (const p of ['$0', '$0 forever', '$9 / month', '€12 per year', '£5.99/mo', '19 USD']) {
      expect(p).toMatch(PLAN_PRICE)
    }
  })

  it('a price appears only when the flag is on AND a price is set', async () => {
    vi.stubEnv('VITE_PRICING_ENABLED', 'true')
    vi.stubEnv('VITE_PRO_PRICE', SENTINEL)
    expect(await textOf(Pricing)).toContain(`Pro ${SENTINEL}`)
    cleanup()
    vi.stubEnv('VITE_PRO_PRICE', '')
    expect(await textOf(Pricing)).not.toMatch(PLAN_PRICE)
  })
})
