# Stratoscan — Secrets Management Runbook

> Tier 2 ops gap #5: env-file secrets are fine for launch, but there was no
> documented rotation path or CI secret wiring. This file closes that gap.

---

## 1. What secrets exist

| Secret | Where it lives in `.env` | Why it matters | Rotation impact |
|---|---|---|---|
| Neon connection strings | `DATABASE_URL`, `BACKTEST_DATABASE_URL`, `NEWS_DATABASE_URL` | Full DB access (users, OHLCV, news) | **Critical** — full data access |
| Finnhub keys | `FINNHUB_KEY_01..10`, `NEWS_FINNHUB_KEY` | External API quota (free tier) | Low — just swap keys |
| LLM keys | `SCREENER_LLM_KEY`, `BACKTEST_LLM_KEY`, `ZAI_KEYS`, `GEMINI_KEYS`, `GROQ_KEYS`, … | Free-tier LLM quota | Low — swap keys |
| Upstash Redis token | `UPSTASH_REDIS_REST_TOKEN` | Shared rate-limit counters | Low-medium |
| SendGrid API key | `SENDGRID_API_KEY` | Sends verification email | Medium — spamming risk |
| JWT signing secret | `JWT_SECRET` | Signs every auth token | **Critical** — invalidates all sessions |

**Ground rule:** `.env` is gitignored and auto-chmod 600 by `src/config.py`.
Never commit it. If you ever paste a secret into a chat/issue/log, rotate it
**immediately** — assume it is compromised.

### `ADMIN_EMAILS` — trust model

`ADMIN_EMAILS` is **not** a secret, but it is a trust boundary: any account
whose email is listed is granted the admin role at **register and login**
(bootstrap, idempotent — see `_bootstrap_admin_role` in `src/api.py`), and
listed addresses also **bypass the email-verification gate** on login.

⚠️ With `EMAIL_VERIFICATION_REQUIRED=false` (the default), *anyone* can
register any address — including one on the `ADMIN_EMAILS` list — so the
list is only as safe as the inboxes on it. Before launch:

1. Set `EMAIL_VERIFICATION_REQUIRED=true` in production, **or**
2. Claim the admin address yourself at deploy time (register it first with
your own password) and keep the list to inboxes you control.

Granting/revoking after the first admin is done via `PATCH /admin/users`
(`is_admin` column), not by editing the env list.

---

## 2. Production CORS (set on the Fly app, not in the repo)

`CORS_ALLOW_ORIGINS` is **env-driven** (`src/config.py` reads it at boot) and
is LOCKED DOWN — no wildcard. The dev default only covers localhost, so in
production you MUST set the real frontend origin on the Fly app or every
browser API call from Vercel will be blocked:

```bash
fly secrets set CORS_ALLOW_ORIGINS="https://your-app.vercel.app"   # or your custom domain
```

- Multiple origins: comma-separated (`https://a.com,https://b.com`).
- A wildcard `"*"` will NOT work: the API uses credentialed requests
  (`allow_credentials=True`), and browsers reject `*` + credentials by spec.
- Verified: production preflight returns 200 and echoes the exact configured
  origin; unlisted origins are never echoed. Locked in by
  `tests/test_docs_gating.py`.
- The deploy workflow (`deploy.yml`) already stages this key from the GitHub
  secret `CORS_ALLOW_ORIGINS`.

---

## 3. Environment template contract

`.env.example` is the single source of truth for every env var the app can
read. **`validate_env_template()`** (called from `validate_config()` in
`src/config.py`) compares the template against every `Settings` field plus
the per-provider LLM vars (`<NAME>_BASE_URL` / `<NAME>_MODEL` / `<NAME>_KEYS`)
and warns if anything the app reads is undocumented:

```bash
python -c "from src.config import validate_env_template; print(validate_env_template())"
# []  <- empty list means the template is complete
```

Rules when adding a new env var:
1. Add the field to `Settings` in `src/config.py` **and** the key to
   `.env.example` in the same change — otherwise validation fails.
2. A commented-out line (`# KEY=value`) in the template still counts as
   documented; an active `KEY=value` line also counts. Both are accepted.
3. The template ships with `<placeholder>` values only — never real secrets.

---

## 4. Rotation procedures

### JWT_SECRET (breaking change — do it deliberately)
1. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Update `.env` → restart the API.
3. **All existing sessions are invalidated** (tokens are signed, not stored).
   Users re-login once. Schedule this during low traffic.

### Neon connection strings
1. Create a new password in the Neon dashboard (or new branch/project).
2. Update `.env` → restart.
3. Verify: `python -m src.main setup` (runs migrations) + `GET /health`
   shows `database_alive: true`.

### Finnhub / LLM / Upstash / SendGrid
1. Generate a fresh key in the provider dashboard.
2. Update `.env` → restart (LLM keys are read at call time; a restart is
   still cleaner to avoid half-loaded state).
3. Rotate the OLD key in the provider dashboard only after the new one
   has been confirmed live in prod for a day.

---

## 5. CI / deploy secret wiring (GitHub Actions)

CI **never needs secrets** — the hermetic test suite requires no network,
no DB, no keys (fakes for everything). This is deliberate: a PR from a fork
cannot exfiltrate or burn quota.

Deployment (a separate workflow or manual `docker run`) reads secrets from
**GitHub Actions secrets**, not `.env`:

```
GitHub → Settings → Secrets and variables → Actions
```

| Repository secret | Maps to | Used by |
|---|---|---|
| `DATABASE_URL` | `DATABASE_URL` | API + scheduler |
| `BACKTEST_DATABASE_URL` | `BACKTEST_DATABASE_URL` | Backtest engine + warmer + backfill |
| `NEWS_DATABASE_URL` | `NEWS_DATABASE_URL` | News pipeline |
| `FINNHUB_KEYS_CSV` | split into `FINNHUB_KEY_01..10` | Screener refresh |
| `NEWS_FINNHUB_KEY` | `NEWS_FINNHUB_KEY` | News fetch |
| `SCREENER_LLM_KEY`, `BACKTEST_LLM_KEY` | same | LLM layers |
| `UPSTASH_REDIS_REST_URL` / `_TOKEN` | same | Rate limiting |
| `SENDGRID_API_KEY` | `SENDGRID_API_KEY` | Email verification |
| `JWT_SECRET` | `JWT_SECRET` | Auth |

In a deploy workflow:

```yaml
- name: Run container with secrets
  run: docker run --rm --env-file <(env) stratoscan
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
    BACKTEST_DATABASE_URL: ${{ secrets.BACKTEST_DATABASE_URL }}
    JWT_SECRET: ${{ secrets.JWT_SECRET }}
    # ... every other secret
```

**Never** store a secret in a workflow file, a Docker ARG baked into an
image, or `.env` committed to the repo.

---

## 6. Alerting delivery (ops)

Alerts (failed background jobs, stale/dead scheduler) deliver through
`src/alerts.py` — two channels, at least one required or alerting is a no-op:

- **`ALERT_WEBHOOK_URL`** (preferred) — a Slack/Discord/ntfy incoming webhook.
  Not a secret in the security sense (it's a POST-only token), but treat it
  like one — it can spam a channel.
- **`ALERT_EMAIL`** — recipient for email alerts when no webhook is set.
  Requires `SENDGRID_API_KEY` + `EMAIL_FROM_ADDRESS` (the same SendGrid
  client used for verification mail). The address should be an ops inbox.

Wire whichever you use in CI/deploy the same way as the table above.

---

## 7. Audit checklist (run before any launch)

- [ ] `.env` absent from git (`git check-ignore .env` → prints it)
- [ ] `.env` permissions are `600` (auto-fixed by config at import)
- [ ] No secrets in `git log`, README, `.env.example`, or pasted in chats
- [ ] `JWT_SECRET` is a fresh random 48-byte value, not a default
- [ ] All provider keys are scoped to free-tier quotas, not org-wide
- [ ] CI runs green with **zero** secrets present (fork-safe)
