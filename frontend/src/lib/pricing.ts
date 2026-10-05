/**
 * The price feature flag (Day 12). No price appears anywhere in the UI until
 * BOTH are set at build time:
 *
 *   VITE_PRICING_ENABLED=true
 *   VITE_PRO_PRICE="…"   (the exact display string, e.g. a monthly amount)
 *
 * Everything price-facing must go through `proPrice()`; `pricing.test.tsx`
 * renders the public pages and Settings with the flag off and fails on any
 * currency amount.
 */
function env(): Record<string, string | undefined> {
  return import.meta.env as Record<string, string | undefined>
}

export function pricingEnabled(): boolean {
  return env().VITE_PRICING_ENABLED === 'true'
}

/** The Pro price to display, or null while prices are switched off. */
export function proPrice(): string | null {
  const price = (env().VITE_PRO_PRICE ?? '').trim()
  return pricingEnabled() && price ? price : null
}
