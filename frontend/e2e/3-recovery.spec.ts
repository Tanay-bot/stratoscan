import { expect, test } from '@playwright/test'
import { API, PASSWORD, apiAccount, inboxCount, linkFromInbox, logIn, uniqueEmail } from './helpers'

/**
 * Journey 3 — recovery: forgot password → reset from the inbox → the session
 * that was open elsewhere is dead → the new password works, the old one
 * doesn't.
 */
test('recovery: forgot, reset, old session dead, login with the new password', async ({
  page,
  browser,
}) => {
  const email = uniqueEmail('reset')
  await apiAccount(email)

  // A session left open on "another device".
  const other = await browser.newContext()
  const otherPage = await other.newPage()
  await logIn(otherPage, email, PASSWORD)
  const oldToken = await otherPage.evaluate(() => {
    for (const k of Object.keys(localStorage)) {
      const v = localStorage.getItem(k) ?? ''
      if (v.split('.').length === 3) return v
    }
    return ''
  })
  expect(oldToken).not.toBe('')

  const before = inboxCount(email)
  await page.goto('/forgot-password')
  await page.locator('#email').fill(email)
  await page.locator('button[type="submit"]').click()
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible()
  const link = await linkFromInbox(email, '/reset-password', before)
  await page.goto(link.replace(/^https?:\/\/[^/]+/, ''))
  const fresh = 'a-brand-new-passphrase-2'
  await page.locator('#new-password').fill(fresh)
  await page.locator('#confirm-password').fill(fresh)
  await page.locator('button[type="submit"]').click()
  await expect(page.getByRole('heading', { name: 'Password updated' })).toBeVisible()

  // The old session is dead — at the API, and in the browser that held it.
  const me = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${oldToken}` } })
  expect(me.status).toBe(401)
  await otherPage.goto('/app/settings')
  await expect(otherPage).toHaveURL(/\/login/)
  await other.close()

  // The reset link is single-use.
  await page.goto(link.replace(/^https?:\/\/[^/]+/, ''))
  await page.locator('#new-password').fill('yet-another-pass-3')
  await page.locator('#confirm-password').fill('yet-another-pass-3')
  await page.locator('button[type="submit"]').click()
  await expect(page.getByRole('heading', { name: /can.t be used/i })).toBeVisible()

  // Old password refused; new one works.
  const oldLogin = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password: PASSWORD }),
  })
  expect(oldLogin.status).toBe(401)
  await logIn(page, email, fresh)
})
