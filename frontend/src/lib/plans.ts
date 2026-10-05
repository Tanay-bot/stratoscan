/**
 * The two plans, for pages that render before anyone signs in (Pricing,
 * Features). A mirror of `stratoscan/src/entitlements.py::PLANS` —
 * `tests/test_entitlements.py` reads this file and fails if the numbers
 * drift. Signed-in surfaces use the live `GET /me/entitlements` instead.
 *
 * No prices here: see `lib/pricing.ts`.
 */
export const PLANS = {
  free: {
    label: 'Free',
    backtestsPerMonth: 50,
    savedStrategies: 3,
    csvExport: false,
    monitoringEmails: false,
  },
  pro: {
    label: 'Pro',
    backtestsPerMonth: null as number | null,
    savedStrategies: 20,
    csvExport: true,
    monitoringEmails: true,
  },
} as const

/** First N accounts are Pro for life (src/entitlements.GRANDFATHER_LIMIT). */
export const GRANDFATHER_LIMIT = 100

/**
 * Never gated, on any plan: everything that makes a number trustworthy.
 * A product that sells trust in tiers is selling something else.
 */
export const NEVER_GATED = [
  '10 years of history on every backtest',
  'Fees, slippage and the SPY benchmark on every result',
  'Monte Carlo, confidence intervals and walk-forward checks',
  'The exact rules and code behind every number',
  'A plain-English reading of every result',
  'Public share links',
] as const
