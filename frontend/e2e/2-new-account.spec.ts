import { expect, test } from '@playwright/test'
import { PASSWORD, logIn, signUp, uniqueEmail, verifyFromInbox, waitForResult } from './helpers'

/**
 * Journey 2 — a new account: sign up → verify from the inbox → backtest →
 * follow the strategy → log out → log back in → the saved strategy is there.
 */
test('new account: signup, verify, backtest, save, logout, login, see saved', async ({ page }) => {
  const email = uniqueEmail('new')
  await signUp(page, email)
  await verifyFromInbox(page, email)
  await logIn(page, email, PASSWORD)

  const query = 'large cap stocks with bullish momentum'
  await page.goto(`/app/backtest?q=${encodeURIComponent(query)}`)
  await waitForResult(page)
  await page.getByRole('button', { name: /follow weekly/i }).click()
  await expect(page.getByRole('button', { name: /following/i })).toBeVisible()

  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page).not.toHaveURL(/\/app/)
  await expect
    .poll(() => page.evaluate(() => Object.keys(localStorage).filter((k) => /token/i.test(k))))
    .toEqual([])

  await logIn(page, email, PASSWORD)
  await page.goto('/app')
  await expect(page.getByText(`“${query}”`)).toBeVisible()
})
