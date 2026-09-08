# Stratoscan — Deployment Runbook

**Stack:** Vercel (frontend) + Fly.io (backend, ~$2–5/mo always-on).

> ℹ️ This runbook documents the **manual** deploy path (great for first setup
> and debugging). In day-to-day use you should NOT run these by hand — the
> GitHub Actions workflows do it for you on every push to `main`:
>
> - `.github/workflows/deploy.yml` — backend → Fly.io (gate → secrets →
>   deploy → /health gate → rollback on failure)
> - `.github/workflows/frontend-deploy.yml` — frontend → Vercel (gate →
>   deploy → healthcheck)
> - `.github/workflows/ci.yml` + `frontend-ci.yml` — hermetic test/lint gates
>   on every push/PR
> - The release gate (behavior probes) is an **opt-in job inside
>   `deploy.yml`**, not its own workflow — enable it with the
>   `run_release_gate` workflow_dispatch input or the `RUN_RELEASE_GATE`
>   repository variable. `stratoscan/RELEASE_GATE.md` owns the probe list
>   (incl. the scheduler-watchdog probe, see `stratoscan/SCHEDULER.md`, and
>   the yfinance data-accuracy probe).
>
> Push to `main` and both deploys happen automatically. The manual commands
> below remain the source of truth for what the workflows execute.

Everything else is external SaaS that already has free tiers:

| Service | Role | Config var |
|---|---|---|
| Neon (Postgres) | screener DB, backtest DB, news DB (3 projects) | `DATABASE_URL`, `BACKTEST_DATABASE_URL`, `NEWS_DATABASE_URL` |
| Upstash Redis | exact cross-worker rate limiting | `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` |
| Finnhub | stock data + news | `FINNHUB_KEY_01..10`, `NEWS_FINNHUB_KEY` |
| ZenMux / LLM providers | NL interpreter + sentiment | `SCREENER_LLM_KEY`, `BACKTEST_LLM_KEY`, `LLM_*` |
| SendGrid | verification email | `SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS` |
| Sentry | error monitoring | `VITE_SENTRY_DSN` (frontend) |

> ⚠️ **One instance only.** The background scheduler (cache warmer, news
> refresh, job heartbeat) is a **separate long-lived process** —
> `python -m src.main scheduler` (see `stratoscan/SCHEDULER.md` for the full
> watchdog story). The container entrypoint starts **only** the API
> (uvicorn); it does **not** start the scheduler. Keep exactly ONE scheduler
> process — do NOT scale to multiple machines or multiple scheduler
> processes, or every instance would run the same jobs.
> `fly.toml` pins `min_machines_running = 1` and `auto_stop_machines = "off"`
> so the machine (and whatever it runs) stays up 24/7.
>
> **Restart on crash:** a crashed API machine is restarted by Fly
> (auto-restart + `/health` check). The scheduler, being a separate process
> the entrypoint does not start, is **not** auto-restarted by that — the
> heartbeat alert (`Scheduler health alert`, via `ALERT_WEBHOOK_URL`) pages
> the operator within ~45 min, and recovery is manual:
>
> ```bash
> # restart the scheduler on the API machine (or a scheduler machine):
> flyctl machines list -a stratoscan-api          # find the machine id
> flyctl machines restart <machine-id> -a stratoscan-api
> # OR, if the scheduler runs as its own Fly machine:
> flyctl machines restart <machine-id> -a stratoscan-scheduler
> ```
>
> To make Fly supervise the scheduler (restart-on-crash covers both), add a
> `[processes]` group to `fly.toml` — see `stratoscan/SCHEDULER.md` §5.2.

---

## 1. Frontend → Vercel (free tier)

### CI/CD (recommended)

`.github/workflows/frontend-deploy.yml` deploys on every push to `main`, and
opens an ephemeral preview deploy on every PR. The manual commands below are
for first-time setup and debugging.

### First deploy (manual — one-time)

```bash
cd frontend
npx vercel login
npx vercel link          # connect this directory to a Vercel project
npm run build
npx vercel --prod
```

### Or import the repo in the dashboard (auto-build on push to `main`)

vercel.com → **Add New → Project → Import Git Repository** → pick the repo:

| Setting | Value |
|---|---|
| Framework preset | Vite (auto-detected) |
| Root directory | `frontend` |
| Build command | `npm run build` |
| Output directory | `dist` |

### Build-time env vars (required)

Project → **Settings → Environment Variables → Production**:

```bash
VITE_API_URL=https://stratoscan-api.fly.dev   # your Fly.io backend origin
VITE_SITE_URL=https://stratoscan.com          # public origin for canonical/OG URLs
VITE_SENTRY_DSN=https://xxxx@o123.ingest.sentry.io/123   # from Sentry (step 3)
VITE_DEMO_MODE=false
```

> Vite bakes `VITE_*` values into the bundle at build time, so these are
> **build env vars**, not runtime secrets. Changing them = new deploy.
> The deploy workflow also forwards them via `--build-env` so the gated
> artifact and the deployed artifact are configured identically.

### Custom domain

Project → **Settings → Domains** → add `app.stratoscan.com` (free SSL auto-issued).
Then add it to the backend CORS allowlist (step 2) and set `VITE_SITE_URL` to match.

### Security headers

Wired in **`frontend/vercel.json`** — CSP, `nosniff`, HSTS, `frame-ancestors 'none'`,
`Referrer-Policy`, `Permissions-Policy`, plus the SPA rewrite. `frontend/public/_headers`
is a portable mirror only: **Vercel does not read `_headers`** (that is a Cloudflare Pages
/ Netlify convention), which is why the headers live in `vercel.json`.
`npm run verify:headers` pins the two in sync and runs in both frontend gates.
CSP `connect-src` must list the backend origin — update it if the API ever moves.
Rationale and the post-deploy `curl` acceptance check live in `frontend/DEPLOYMENT.md`.

---

## 2. Backend → Fly.io (always-on, ~$2–5/mo)

### CI/CD (recommended — replaces the manual flow below)

`.github/workflows/deploy.yml` deploys the backend automatically on every
push to `main` (or via **Actions → Backend Deploy → Run workflow**):

1. **Gate** — hermetic suites + `py_compile`. Nothing deploys unless green.
2. **Secrets** — stages every env var from GitHub Secrets via
   `fly secrets import --stage` (merges; unset GitHub secrets are skipped so
   live Fly values are never wiped). Requires a `FLY_API_TOKEN` GitHub secret
   (Dashboard → Account → Access Tokens).
3. **Deploy** — `fly deploy --remote-only`. The container entrypoint runs
   `alembic upgrade head` (retried ×5 for cold Neon) before uvicorn starts,
   so **migrations are part of every deploy**.
4. **Healthcheck gate** — polls `/health` until 200 (~5 min budget).
5. **Rollback** — if `/health` never returns 200, `fly rollback` restores the
   previous release and the run fails loudly.

### First deploy (manual — one-time)

```bash
cd stratoscan
fly launch --no-deploy --name stratoscan-api   # uses fly.toml + Dockerfile
```

Then set **every** secret (see the full table below):

```bash
fly secrets set \
  DATABASE_URL="postgresql://..." \
  BACKTEST_DATABASE_URL="postgresql://..." \
  NEWS_DATABASE_URL="postgresql://..." \
  JWT_SECRET="$(openssl rand -hex 32)" \
  FINNHUB_KEY_01="..." \
  SCREENER_LLM_KEY="..." \
  BACKTEST_LLM_KEY="..." \
  UPSTASH_REDIS_REST_URL="..." \
  UPSTASH_REDIS_REST_TOKEN="..." \
  REDIS_RATE_LIMIT_ENABLED="true" \
  CORS_ALLOW_ORIGINS="https://stratoscan.vercel.app,https://app.stratoscan.com" \
  SENDGRID_API_KEY="SG.xxx" \
  EMAIL_FROM_ADDRESS="noreply@stratoscan.com" \
  APP_BASE_URL="https://app.stratoscan.com" \
  ADMIN_EMAILS="you@stratoscan.com" \
  ALERT_WEBHOOK_URL="https://hooks.slack.com/..." \
  LOG_LEVEL="INFO" \
  LOG_FORMAT="json"
```

Deploy + verify:

```bash
fly deploy
fly open
curl -s https://stratoscan-api.fly.dev/health
```

### Backend env var reference (from `src/config.py`)

| Var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | ✅ | Neon screener DB — **cold start on first connect after suspend** (entrypoint retries migrations ×5 for this) |
| `BACKTEST_DATABASE_URL` | ✅ prod | Dedicated Neon DB for 10yr OHLCV; unset → SQLite fallback (dev only) |
| `NEWS_DATABASE_URL` | for news | Separate Neon DB for articles + sentiment |
| `JWT_SECRET` | ✅ | Auth. `openssl rand -hex 32`. Rotating it logs everyone out (by design). |
| `FINNHUB_KEY_01..10` | ✅ | ≥1 key; 10 for round-robin |
| `SCREENER_LLM_KEY` | ✅ | NL interpreter (Layer 1) |
| `BACKTEST_LLM_KEY` | optional | Backtester falls back to rule-based |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_PROVIDERS` | optional | Defaults to ZenMux free GLM-4.7-flash |
| `UPSTASH_REDIS_REST_URL` + `_TOKEN` + `REDIS_RATE_LIMIT_ENABLED=true` | for exact limits | In-memory fallback if unset/unreachable |
| `CORS_ALLOW_ORIGINS` | ✅ prod | Comma-separated; must include your Pages domain. LOCKED DOWN — no wildcard. |
| `SENDGRID_API_KEY` + `EMAIL_FROM_ADDRESS` | for verification | Sender must be verified in SendGrid |
| `APP_BASE_URL` | prod | Origin used to build the verify-email link |
| `EMAIL_VERIFICATION_REQUIRED` | optional | `true` blocks unverified logins |
| `ADMIN_EMAILS` | optional | Comma-separated; auto-promotes at login |
| `ALERT_WEBHOOK_URL` | optional | Slack/Discord/ntfy for failed jobs |
| `LOG_FORMAT=json` | prod | Structured logs (`LOG_LEVEL=INFO`) |
| `RATE_LIMIT_ENABLED` | ✅ | Keep `true` in prod (set in fly.toml env) |

---

## 3. Sentry (error monitoring)

### What I need from you — just the **DSN** (it's public, not a secret)

1. In Sentry → **Create Project** → choose **React** → name it `stratoscan-frontend` → **Create**.
2. Go to **Settings → Projects → stratoscan-frontend → Client Keys (DSN)**.
3. Copy the DSN (looks like `https://abcdef123@o12345.ingest.sentry.io/4500000000000000`).

### Where it goes

- **Option A (recommended):** set it as the `VITE_SENTRY_DSN` GitHub secret (the
  deploy workflow forwards it to Vercel's build), or add it directly in the Vercel
  dashboard under Settings → Environment Variables → redeploy.
- **Option B (local dev):** put it in `frontend/.env.local` as
  `VITE_SENTRY_DSN=...` (`.env.local` is gitignored).

That's the entire handoff — no MCP server needed. The `@sentry/react` SDK is
already installed and initialized in `frontend/src/main.tsx`, **disabled by
default** (empty DSN = no-op), with:

- 10% trace sampling (free-tier friendly), `environment: production` from Vite.
- A global `<Sentry.ErrorBoundary>` that renders a branded "Something went
  wrong" fallback instead of a white screen — this also closes the
  "no ErrorBoundary" P0 gap.
- `beforeSend` strips any auth/token data if it ever leaks into an error.

### Optional: sourcemaps (better stack traces)

Set `SENTRY_AUTH_TOKEN` (Sentry → Settings → Developer Settings → Auth Tokens,
needs `project:releases` scope) in the Pages build env and add
`@sentry/vite` to the build — I can wire that up on request.

---

> ℹ️ **Caveat:** `DEPLOY.md` at the repo root is the source of truth for the
> manual flow. The GitHub Actions workflows (`.github/workflows/*.yml`) are
> the automated equivalent — when they and this document disagree, the
> workflow files win (they are what actually runs).

## 4. Day-2 checklist

- [ ] `fly.toml` `primary_region` — keep `iad` or pick your closest region.
- [ ] Custom domain on Pages + `CORS_ALLOW_ORIGINS` includes it.
- [ ] `LOG_FORMAT=json` + a log drain (`fly logs`) or Axiom/Datadog later.
- [ ] Set `ALERT_WEBHOOK_URL` (Slack/Discord/ntfy) — this is the scheduler
      watchdog's page; without it a dead scheduler is silent.
- [ ] Add the `[processes]` group to `fly.toml` (api + scheduler) so Fly
      restart-on-crash covers the scheduler too (see `stratoscan/SCHEDULER.md` §5.2).
