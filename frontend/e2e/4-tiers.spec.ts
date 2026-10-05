import { expect, test } from '@playwright/test'
import { API, PASSWORD, apiAccount, logIn, uniqueEmail } from './helpers'

/**
 * Journey 4 — tiers: a Free account cannot export; an admin sets it to Pro
 * and export works on the next page load; set back to Free, access ends —
 * in the UI and at the API.
 */
test('tiers: admin sets Pro → gated features unlock → back to Free → access ends', async ({
  page,
}) => {
  // admin@e2e.test is promoted by ADMIN_EMAILS; it may exist from an earlier local run.
  const adminToken = await apiAccount('admin@e2e.test').catch(async () => {
    const r = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'admin@e2e.test', password: PASSWORD }),
    })
    return ((await r.json()) as { access_token: string }).access_token
  })
  const email = uniqueEmail('tier')
  const userToken = await apiAccount(email)
  const me = (await (
    await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${userToken}` } })
  ).json()) as { id: number }
  const setPlan = (plan: string) =>
    fetch(`${API}/admin/users/${me.id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${adminToken}` },
      body: JSON.stringify({ plan }),
    })
  const exportCsv = () =>
    fetch(`${API}/screen/export`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${userToken}` },
      body: JSON.stringify({ query: 'large cap technology stocks' }),
    })

  const screen = async () => {
    await page.goto('/app/screener')
    await page.getByPlaceholder(/undervalued small-cap/i).fill('large cap technology stocks')
    await page.getByRole('button', { name: /^screen$/i }).click()
    const btn = page.getByRole('button', { name: /^export$/i })
    await expect(btn).toBeVisible({ timeout: 60_000 })
    return btn
  }

  await logIn(page, email)
  await page.goto('/app/settings')
  await expect(page.getByRole('region', { name: 'Plan' })).toContainText('Free plan')
  await expect(await screen()).toBeDisabled()
  expect((await exportCsv()).status).toBe(403)

  // An admin's change applies on the next request — no re-login.
  expect((await setPlan('pro')).status).toBe(200)
  await page.goto('/app/settings')
  await expect(page.getByRole('region', { name: 'Plan' })).toContainText('Pro plan')
  expect((await exportCsv()).status).toBe(200)
  const exportBtn = await screen()
  await expect(exportBtn).toBeEnabled()
  const download = page.waitForEvent('download')
  await exportBtn.click()
  expect((await download).suggestedFilename()).toMatch(/\.csv$/)

  expect((await setPlan('free')).status).toBe(200)
  const after = await exportCsv()
  expect(after.status).toBe(403)
  expect(((await after.json()) as { upgrade_required: string }).upgrade_required).toBe('csv_export')
  await page.goto('/app/settings')
  await expect(page.getByRole('region', { name: 'Plan' })).toContainText('Free plan')
  await expect(await screen()).toBeDisabled()
})
