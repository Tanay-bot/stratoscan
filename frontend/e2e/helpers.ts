import fs from 'node:fs'
import { expect, type Page } from '@playwright/test'

export const API = process.env.E2E_API ?? 'http://127.0.0.1:8100'
export const PASSWORD = 'correct-horse-battery-1'

/** A fresh address per run: the E2E database lives across local runs. */
export const uniqueEmail = (tag: string) =>
  `${tag}.${Date.now()}.${Math.floor(Math.random() * 1e6)}@e2e.test`

interface Mail {
  to: string[]
  subject: string
  text: string
}

/** Wait for an email to `to` (from the stack's SMTP sink) and return the first link matching `pathPart`. */
export async function linkFromInbox(to: string, pathPart: string, after = 0): Promise<string> {
  const file = process.env.E2E_MAIL_LOG!
  let found = ''
  await expect
    .poll(
      () => {
        if (!fs.existsSync(file)) return ''
        const mails = fs
          .readFileSync(file, 'utf8')
          .split('\n')
          .filter(Boolean)
          .map((l) => JSON.parse(l) as Mail)
          .filter((m) => m.to.some((t) => t.toLowerCase() === to.toLowerCase()))
          .slice(after)
        for (const m of mails.reverse()) {
          const link = m.text.match(new RegExp(`https?://\\S*${pathPart}\\S*token=[\\w\\-.~%]+`))
          if (link) return (found = link[0])
        }
        return ''
      },
      { timeout: 30_000, message: `no "${pathPart}" email reached ${to}` },
    )
    .not.toBe('')
  return found
}

export function inboxCount(to: string): number {
  const file = process.env.E2E_MAIL_LOG!
  if (!fs.existsSync(file)) return 0
  return fs
    .readFileSync(file, 'utf8')
    .split('\n')
    .filter(Boolean)
    .filter((l) => (JSON.parse(l) as Mail).to.some((t) => t.toLowerCase() === to.toLowerCase()))
    .length
}

export async function signUp(page: Page, email: string, password = PASSWORD) {
  await page.goto('/signup')
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(password)
  await page.locator('#confirm').fill(password)
  await page.locator('button[type="submit"]').click()
}

export async function verifyFromInbox(page: Page, email: string) {
  const link = await linkFromInbox(email, '/verify-email')
  await page.goto(link.replace(/^https?:\/\/[^/]+/, ''))
  await expect(page.getByRole('heading', { name: 'Email verified' })).toBeVisible()
}

export async function logIn(page: Page, email: string, password = PASSWORD) {
  await page.goto('/login')
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(password)
  await page.locator('button[type="submit"]').click()
  await expect(page).toHaveURL(/\/app/)
}

/** Register + verify through the API (for journeys whose subject is something else). */
export async function apiAccount(email: string): Promise<string> {
  const r = await fetch(`${API}/auth/register`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password: PASSWORD }),
  })
  if (!r.ok) throw new Error(`register ${email}: ${r.status} ${await r.text()}`)
  const link = await linkFromInbox(email, '/verify-email')
  const token = new URL(link).searchParams.get('token')
  const v = await fetch(`${API}/auth/verify-email?token=${encodeURIComponent(token!)}`)
  if (!v.ok) throw new Error(`verify ${email}: ${v.status}`)
  const login = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password: PASSWORD }),
  })
  return ((await login.json()) as { access_token: string }).access_token
}

/** Wait for a backtest result on the page (the share button enables when one is in). */
export async function waitForResult(page: Page) {
  await expect(page.getByRole('heading', { name: /what happened/i })).toBeVisible({
    timeout: 120_000,
  })
}
