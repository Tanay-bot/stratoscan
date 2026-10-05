import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { apiAccount, logIn, uniqueEmail, waitForResult } from './helpers'

/**
 * The accessibility sweep (Day 13), measured rather than estimated:
 *   - axe-core (WCAG 2.0/2.1 A + AA rules) on every key page, including a
 *     real backtest result and the signed-in app — zero serious or critical
 *     violations allowed;
 *   - keyboard-only traversal: Tab reaches the primary actions in order, and
 *     every focused element shows a visible focus indicator;
 *   - charts carry an accessible name (role="img" + label).
 * Contrast of the text tokens over every glass panel is measured by
 * src/lib/contrast.test.ts; axe re-checks it here on the rendered pages.
 */
async function axe(page: Page, name: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  const bad = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  const summary = bad.map(
    (v) => `${v.id} (${v.impact}): ${v.nodes.length}× — ${v.nodes[0]?.target.join(' ')}`,
  )
  expect(summary, `${name}: axe found serious/critical issues`).toEqual([])
}

const PUBLIC = [
  '/',
  '/pricing',
  '/features',
  '/strategies',
  '/verification',
  '/login',
  '/signup',
  '/risk',
]

for (const path of PUBLIC) {
  test(`axe: ${path}`, async ({ page }) => {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await axe(page, path)
  })
}

test('axe: a real backtest result (trial) and its explanation', async ({ page }) => {
  await page.goto('/try?q=' + encodeURIComponent('oversold large cap stocks with RSI below 30'))
  await waitForResult(page)
  await page.waitForTimeout(1500) // let charts and toasts settle before scanning
  await axe(page, '/try result')
  const charts = page.locator('[role="img"]')
  expect(await charts.count()).toBeGreaterThan(0)
  for (const chart of await charts.all()) {
    const label = (await chart.getAttribute('aria-label')) ?? ''
    expect(label.length, 'every chart has a text summary').toBeGreaterThan(10)
  }
})

test('axe: the signed-in app', async ({ page }) => {
  const email = uniqueEmail('a11y')
  await apiAccount(email)
  await logIn(page, email)
  for (const path of ['/app', '/app/settings', '/app/screener']) {
    await page.goto(path)
    // The app keeps a sync/poll connection open, so it never goes network-idle.
    await expect(page.locator('main h1').first()).toBeVisible()
    await page.waitForTimeout(1500)
    await axe(page, path)
  }
})

test('keyboard only: skip link first, primary actions reachable, focus always visible', async ({
  page,
}) => {
  await page.goto('/')
  await page.keyboard.press('Tab')
  const first = page.locator(':focus')
  await expect(first).toHaveText(/skip to content/i)

  const seen: string[] = []
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press('Tab')
    // .glass-hover animates every property (300ms), the focus outline too.
    await page.waitForTimeout(350)
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      if (!el || el === document.body) return null
      const s = getComputedStyle(el)
      const ring =
        (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) ||
        (s.boxShadow && s.boxShadow !== 'none')
      return { text: (el.textContent ?? '').trim().slice(0, 40), ring }
    })
    if (!info) continue
    expect(info.ring, `no visible focus on "${info.text}"`).toBeTruthy()
    seen.push(info.text)
  }
  expect(seen.some((t) => /get started|try it free/i.test(t))).toBeTruthy()
  expect(seen.some((t) => /oversold mean reversion/i.test(t))).toBeTruthy()
})
