import { defineConfig } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

/**
 * End-to-end journeys (Day 13) against a REAL stack: the FastAPI app over a
 * throwaway local Postgres seeded with synthetic prices (stratoscan/e2e_stack.py),
 * and this frontend built against it. No network, no paid service, no mocks.
 *
 *   npx playwright test            # starts both servers, runs every journey
 *
 * Needs a Postgres server (E2E_ADMIN_DSN; CI uses a service container).
 */
const API = 'http://127.0.0.1:8100'
const WEB = 'http://127.0.0.1:4174'
process.env.E2E_MAIL_LOG ??= path.resolve(here, '../stratoscan/data/e2e_mail.jsonl')
process.env.E2E_API ??= API

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: WEB,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.PW_CHROMIUM_PATH
      ? { executablePath: process.env.PW_CHROMIUM_PATH }
      : undefined,
  },
  webServer: [
    {
      command: 'python e2e_stack.py',
      cwd: path.resolve(here, '../stratoscan'),
      url: `${API}/health`,
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
      env: { E2E_MAIL_LOG: process.env.E2E_MAIL_LOG!, E2E_WEB_ORIGIN: WEB },
      stdout: 'pipe',
    },
    {
      command:
        'npm run build:e2e && npx vite preview --outDir dist-e2e --host 127.0.0.1 --port 4174 --strictPort',
      url: WEB,
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
      env: { VITE_API_URL: API, VITE_DEMO_MODE: 'false' },
    },
  ],
})
