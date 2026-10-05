import { expect, test } from '@playwright/test'
import { waitForResult } from './helpers'

/**
 * Journey 1 — a stranger: land → pick a template → a real backtest with no
 * account → share it → open the link in a clean browser (no cookies, no
 * storage) and see the same result.
 */
test('anonymous: land, template, backtest, share, open the link elsewhere', async ({
  page,
  browser,
}) => {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/')
  await page.getByRole('button', { name: 'Oversold mean reversion' }).click()
  await expect(page).toHaveURL(/\/try\?q=oversold/)
  await expect(page.getByText(/free trial, no signup/i)).toBeVisible()

  await waitForResult(page)
  const share = page.getByRole('button', { name: /share result/i })
  await expect(share).toBeEnabled()
  await share.click()
  await expect(page.getByRole('button', { name: /stop sharing/i })).toBeVisible()
  const url = await page.evaluate(() => navigator.clipboard.readText())
  expect(url).toMatch(/\/r\/[\w-]{16,}$/)

  // A clean context: nobody signed in, nothing in storage.
  const stranger = await browser.newContext()
  const shared = await stranger.newPage()
  const res = await shared.goto(url)
  expect(res?.status()).toBe(200)
  await expect(shared.locator('body')).toContainText('oversold large cap stocks with RSI below 30')
  await expect(shared.locator('body')).toContainText(/not investment advice/i)
  // The public page carries no trade log or account detail, and runs no script.
  expect(await shared.locator('script').count()).toBe(0)
  await stranger.close()
})
