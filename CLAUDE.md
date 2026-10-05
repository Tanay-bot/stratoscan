# Stratoscan — Claude Code Companion

> **Has your investing idea ever worked?** Describe it in plain English; Stratoscan turns it into
> rules and replays them over 10 years of daily prices. It doesn't tell you what to buy — it shows
> you whether what you already believe has ever been true (Day 14 positioning).
>
> This file is the single source of truth for working in this repository. It is deliberately
> long: read the section you need, not the whole thing.

> **Owner:** this file is the source of truth for how this repo is built and the rules for changing it — architecture, module reference, schemas, gotchas.
> HTTP contract → `stratoscan/FRONTEND_API.md`. Deploying → `DEPLOY.md`; going live (drills, production-only checks, rollback, incidents, costs) → `RUNBOOK.md`. Scheduler → `stratoscan/SCHEDULER.md`. Release gate → `stratoscan/RELEASE_GATE.md`. Frontend visual system → `DESIGN.md`.

---

## Table of contents

1. [Repository at a glance](#1-repository-at-a-glance)
2. [Golden rules (non-negotiables)](#2-golden-rules-non-negotiables)
3. [Architecture overview](#3-architecture-overview)
4. [Directory map](#4-directory-map)
5. [Backend: processes & entry points](#5-backend-processes--entry-points)
6. [Backend module reference](#6-backend-module-reference)
7. [Database schema reference](#7-database-schema-reference)
8. [Key end-to-end request flows](#8-key-end-to-end-request-flows)
9. [The scheduler, job monitor & watchdog](#9-the-scheduler-job-monitor--watchdog)
10. [Rate limiting design](#10-rate-limiting-design)
11. [Frontend architecture](#11-frontend-architecture)
12. [API contract essentials](#12-api-contract-essentials)
13. [Configuration & environment variables](#13-configuration--environment-variables)
14. [Testing & verification](#14-testing--verification)
15. [CI/CD & deployment](#15-cicd--deployment)
16. [Common development tasks](#16-common-development-tasks)
17. [Cost controls & operational model](#17-cost-controls--operational-model)
18. [Gotchas & landmines](#18-gotchas--landmines)
19. [Recent significant changes](#19-recent-significant-changes)

---

## 1. Repository at a glance

Stratoscan is a monorepo with two top-level packages plus CI:

| Path | What it is | Tech |
|---|---|---|
| `stratoscan/` | Backend: FastAPI REST API, NL interpreter, screener, backtesting engine, scheduler, news pipeline | Python 3.14, FastAPI, psycopg2, pandas/numpy, yfinance, finnhub-python, alembic, schedule |
| `frontend/` | Frontend: SPA with marketing pages + app shell | React 18, TypeScript (strict), Vite, Tailwind, Zustand, Recharts, Framer Motion, Radix UI |
| `.github/workflows/` | 5 CI/CD workflows | ci.yml, frontend-ci.yml, e2e.yml, deploy.yml, frontend-deploy.yml |

**Product loop:** user types plain English ("find undervalued small-cap tech stocks with rising
momentum") → an LLM (or a deterministic regex parser) converts it to structured filter criteria
→ the screener filters a local Postgres universe of ~5,000 US stocks (zero API calls) → the
backtester simulates the strategy on 10 years of daily OHLCV with fees, slippage, stops, Monte
Carlo, walk-forward and a SPY benchmark.

**Two plans, and the numbers are never paywalled (Day 12).** Free (forever) and Pro. Exactly four
capabilities differ — backtests per month, saved-strategy count, CSV export, weekly strategy
emails — all decided by ONE function, `src/entitlements.plan_for(user)`; accuracy (history,
robustness, the code behind a result, explanations, share pages) is identical on both. No payment
provider yet: an admin sets plans by hand (`src/billing.ManualBillingProvider`), the first 100
accounts are Pro for life, and no price renders until the frontend price flag is on. See
`stratoscan/BILLING.md`.

External services: **Neon** (3 PostgreSQL projects), **Upstash Redis** (exact cross-worker rate
limits, optional), **Finnhub** (quotes/profiles/news, 10 free keys round-robin), **yfinance**
(10y OHLCV for backtests), free-tier **LLM providers** (Z.ai GLM flash default; Gemini, Groq,
ZenMux as failover), **SendGrid** (verification + alert emails), **Sentry** (frontend only).

---

## 2. Golden rules (non-negotiables)

These are the constraints that, if violated, break the product or its cost model. **Read before
editing anything.**

1. **One scheduler process, ever.** `python -m src.main scheduler` must run exactly once. The
   container entrypoint runs one role per process (`api` = migrations + uvicorn, `scheduler`);
   on Fly they are two process groups (`fly.toml [processes]`), scaled `app=1 scheduler=1`.
   Since Day 15 a lease enforces it: a second scheduler is refused at startup (exit 3 + alert)
   while the first's lease (`scheduler_heartbeat.owner`) is fresh. Never add code that spawns a
   second scheduler, and never "fix" deploy scaling by adding machines.
2. **The four-layer pipeline costs are sacred.** Screener = 0 API calls per query. Interpreter =
   ≤1 LLM call per unique query (cached 24h). Backtest = yfinance downloads (cached in the
   backtest DB). Never call an external API per-stock in a hot path.
3. **Neon auto-suspend is a design feature.** The scheduler closes DB pools after every cycle so
   Neon can suspend. Don't hold connections open in long-running processes. Job-monitor heartbeat
   persistence is throttled to `JOB_HEARTBEAT_PERSIST_MINUTES` (15) for the same reason; keep it
   throttled.
4. **Every env var the app reads must be documented in `.env.example`.** `validate_env_template()`
   enforces this; adding a `Settings` field without documenting it fails config validation.
5. **No secrets in code.** All keys via `.env` / platform secrets. `.env` must stay `chmod 600`
   (enforced at import).
6. **Backtest math is frozen.** `tests/test_backtest_golden.py` pins exact numbers. Any change to
   fees, slippage, signal generation, position sizing or portfolio logic must update that file or
   CI fails. `verify_backtest_accuracy.py` + `verify_trade_pnl.py` re-derive every number from
   first principles.
7. **Hermetic tests only, unless the task is explicitly a live probe.** The backend suites
   fake the LLM (`tests/fakes.py::FakeLLMRouter`) and the DB (`FakeUserDB`, `FakeQualityDB`). Live
   verification happens in the `verify_*.py` scripts / `run_probes.py`, never in the unit suites.
8. **SQL injection is impossible by construction.** Column names from the interpreter resolve
   against `STOCK_MASTER_COLUMNS` whitelist before interpolation; values are always parameterized.
   Keep it that way.
9. **Docs are disabled in production.** `APP_ENV=production` kills `/docs`, `/redoc`,
   `/openapi.json`. Don't re-enable them.
10. **The API docs (Swagger/OpenAPI) are the contract.** The frontend's `types.ts` mirrors it;
    `FRONTEND_API.md` documents it. Changing a response shape requires updating all three.
11. **Rate-limit scopes are cost shields.** `screen`/`enrich`/`backtest`/`refresh`/`register`
    caps protect LLM/Finnhub/SendGrid budget. Only `read` is env-configurable
    (`RATE_LIMIT_PER_MINUTE`/`RATE_LIMIT_PER_DAY`).
12. **No raw exception text in API responses.** Errors go through `http_exception_handler` /
    `generic_exception_handler`; log the detail, return a generic message.

---

## 3. Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│                 React + Vite Frontend (Vercel)              │
│  Landing · Screener · Backtest · News · Dashboard · Admin   │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST/JSON over axios (VITE_API_URL)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI Backend (Python 3.14, Fly.io)          │
│                                                             │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐ │
│  │    Auth    │ │ Interpreter│ │  Screener  │ │Backtester│ │
│  │ (JWT, PBKDF2,│ │ (LLM + det.)│ │ (SQL over  │ │ (10y sim │ │
│  │ email verify)│ │ hybrid)    │ │  Postgres) │ │ + robus.)│ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬─────┘ │
│        │              │              │              │       │
│  ┌─────▼──────────────▼──────────────▼──────────────▼───┐  │
│  │        PostgreSQL — THREE dedicated Neon projects     │  │
│  │  • main:    stock_master, users, job_runs,            │  │
│  │            scheduler_heartbeat, user_data,            │  │
│  │            backtest_jobs, events, error_log           │  │
│  │  • backtest: historical_prices, backtest_results      │  │
│  │  • news:    stock_sentiment, news_articles            │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │   LLM    │ │ Finnhub  │ │ yfinance │ │ Upstash  │       │
│  │  Router  │ │ Pool     │ │ (OHLCV)  │ │ Redis    │       │
│  │(failover)│ │(10 keys) │ │          │ │ (limits) │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### The four-layer pipeline

| Layer | Module | What it does | Cost per query |
|---|---|---|---|
| 1. NL Interpreter | `src/interpreter.py`, `src/deterministic_interpreter.py`, `src/llm_router.py` | Plain English → structured `InterpretedQuery`. Deterministic parser first (confidence ≥ 0.6), LLM fallback. | sub-ms + $0 for ~80%; 1 LLM call otherwise (cached 24h) |
| 2. Screener | `src/screener.py` + `PostgresDB` | Filters ~5,000-row `stock_master` with SQL; composite scoring; staleness/completeness guards | **0 API calls** |
| 3. Backtester | `src/backtester.py` | Simulates strategy on 10y daily OHLCV with fees/slippage/stops/shorts; Monte Carlo, walk-forward, parameter grid, portfolio aggregation, SPY benchmark | yfinance download per symbol (cached in backtest DB) |
| 4. Enrichment / News | `src/enrichment.py`, `src/news.py` | News + LLM-scored sentiment | Batch jobs, 0 calls per query |

---

## 4. Directory map

```
stratoscan/                          # Backend (Python)
├── src/
│   ├── api.py               # ALL FastAPI routes + rate-limit middleware (3,816 lines)
│   ├── main.py              # CLI entry: screen/backtest/refresh/scheduler/serve/...
│   ├── config.py            # Settings (pydantic-settings) + validate_config + .env security
│   ├── auth.py              # PBKDF2 hashing + HMAC JWT + FastAPI deps (get_current_user, require_admin)
│   ├── interpreter.py       # NL → InterpretedQuery (LLM + hybrid deterministic path)
│   ├── deterministic_interpreter.py  # regex/dictionary parser w/ confidence scoring
│   ├── llm_router.py        # Multi-provider, multi-key failover with cooldowns
│   ├── screener.py          # DB filtering, composite score, contradiction resolution, auto-relax
│   ├── backtester.py        # Simulation engine + robustness + portfolio (2,570 lines)
│   ├── database.py          # PostgresDB / NewsDB / BacktestDB / SQLiteDB + all schemas
│   ├── data_fetcher.py      # Finnhub pool, symbol universe, refresh pipelines, market-cap sanity
│   ├── scheduler.py         # Background refresh jobs (price/technical/fundamental/news/cache)
│   ├── job_monitor.py       # job_runs table, heartbeat, staleness + failure alerts
│   ├── cache_warmer.py      # Pre-fetch 10y OHLCV for top-N symbols into backtest DB
│   ├── backfill.py          # Bulk OHLCV backfill into Neon backtest DB (resumable)
│   ├── cache.py             # Thread-safe TTL cache (interpret + screen caches)
│   ├── redis_ratelimit.py   # Upstash REST client + RateLimitStore (Redis / in-memory)
│   ├── news.py              # News fetch + LLM sentiment scoring pipeline
│   ├── enrichment.py        # Per-query screen + news/sentiment enrichment
│   ├── emails.py            # SendGrid verification email (stdlib urllib)
│   ├── alerts.py            # Webhook/email alerts (fire-and-forget, throttled)
│   ├── metrics.py           # Prometheus /metrics (stdlib exporter) + middleware
│   ├── admin_dashboard.py   # Self-contained HTML admin dashboard
│   ├── share_page.py        # Day 6: public share summary (field whitelist) + server-rendered page
│   ├── share_card.py        # Day 7: 1200×630 PNG preview card (Pillow + assets/fonts, cached by content hash)
│   ├── strategy_templates.py # Day 8: the 15-template library (served in /meta; mirrored in frontend/src/lib/templates.ts)
│   ├── digest.py            # Day 10: weekly saved-strategy email (light re-run, honest copy, send guarantees)
│   ├── explain.py           # Day 11: plain-English reading of a result (facts whitelist, findings, guarded LLM narrative)
│   ├── entitlements.py      # Day 12: plan_for(user) — the ONE entitlement decision; PLANS, grace, grandfathering
│   ├── billing.py           # Day 12: BillingProvider interface + ManualBillingProvider (admin sets plans)
│   └── logging_config.py    # Text/JSON logging setup
├── tests/                   # hermetic suites, tests/test_*.py (run: python -m tests.test_<name>)
│   └── fakes.py             # FakeLLMRouter, FakeUserDB, FakeQualityDB
├── verify_*.py              # live behavior probes (release gate; run_probes.py picks the set)
├── run_probes.py            # Probe runner against isolated stratoscan_probes DB
├── e2e_stack.py             # Day 13: throwaway local stack for E2E (seeded Postgres, SMTP sink, API)
├── load_test.py             # Day 13: concurrency ramp on /screen + /backtest/async (→ LOAD.md)
├── accuracy_loop.py         # 30-query accuracy verification loop
├── rule_execution_loop.py   # Rule-execution ground-truth tests
├── alembic/                 # Migrations (20 versions)
├── Dockerfile, docker-entrypoint.sh
├── fly.toml, fly.staging.toml
├── .env.example, staging.env(.example)
├── requirements.txt / requirements-lock.txt / pyproject.toml
├── openapi.json / openapi.pretty.json   # generated contract snapshots
└── *.md                    # README, DEPLOY, SCHEDULER, RELEASE_GATE, STAGING, SECRETS,
                            # FAILURE_DRILLS, FRONTEND_API, TESTING_PLAN

frontend/                            # Frontend (React + Vite)
├── src/
│   ├── main.tsx             # Entry: Sentry init, analytics, render
│   ├── App.tsx              # Router + lazy-loaded pages + global providers
│   ├── pages/               # Landing, Screener, Backtest (1,100+ lines), News, Dashboard,
│   │                        # Settings, Auth, VerifyEmail, StockDetail, legal, 404
│   ├── components/          # backtest/, charts/, ui/ (Radix), landing/, layout/ (AppShell)
│   ├── lib/
│   │   ├── api/             # client.ts (axios+interceptors), services.ts (typed calls),
│   │   │                    # types.ts (contract mirror), schemas.ts (zod), demo.ts (mock data)
│   │   ├── backtestParams.ts# shareable-link serialize/parse
│   │   ├── seo.ts, webVitals.ts, safeUrl.ts, series.ts, utils.ts
│   ├── store/               # auth.ts, personal.ts, rateLimit.ts, notifications.ts (Zustand)
│   └── hooks/               # useKeyboardShortcuts, useReducedMotion, useContainerWidth
├── public/                  # _headers (CSP), _redirects (SPA fallback)
├── e2e/                     # Day 13: Playwright journeys (anonymous, new account, recovery, tiers) + axe sweep
├── vite.config.ts, vitest.config.ts, tailwind.config.js, playwright.config.ts
└── package.json

.github/workflows/           # ci.yml, frontend-ci.yml, e2e.yml, deploy.yml, frontend-deploy.yml
```

---

## 5. Backend: processes & entry points

There are **three** long-lived things in the backend; know which is which:

| Process | Command | Purpose |
|---|---|---|
| API server | `python run.py` (= `uvicorn src.api:app`) | Serves all HTTP endpoints on :8000. **Does not** start the scheduler. |
| Scheduler | `python -m src.main scheduler` | Runs background data jobs. Separate process by design. Run exactly one. |
| CLI commands | `python -m src.main <cmd>` | `setup`, `screen`, `backtest`, `refresh`, `refresh-news`, `warm-cache`, `backfill-history`, `enrich`, `serve` |

`src/main.py` is an argparse CLI dispatcher. Each command imports its dependencies lazily
inside the handler (keeps startup fast and keeps the API import graph light). The container
entrypoint (`docker-entrypoint.sh`) runs `alembic upgrade head` (retried ×5 for cold Neon),
then starts uvicorn **only** — the scheduler is supervised separately (see §9).

`src/api.py` at import time: configures logging, builds the FastAPI app (docs disabled when
`settings.is_production`), adds CORS → RateLimitMiddleware → MetricsMiddleware (metrics
outermost so it counts 429s), and creates the module-level `_screen_cache`.

### Running locally

```bash
# Backend (needs .env with DATABASE_URL etc.)
cd stratoscan && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys
python -m src.main setup      # alembic upgrade head + backtest/news schemas
python run.py                 # API on :8000
# in a second terminal:
python -m src.main scheduler  # data refresh jobs

# Frontend
cd frontend && npm install
# .env: VITE_API_URL=http://localhost:8000 (or VITE_DEMO_MODE=true for no backend)
npm run dev                   # on :5173, proxies /api → :8000
```

---

## 6. Backend module reference

### `src/config.py` — settings & validation

- `Settings(BaseSettings)` — every config value is a typed field read from env (`.env` via
  `load_dotenv` + pydantic-settings). `extra = "allow"` so per-provider LLM vars pass through.
- `.env` permissions are checked/locked to `0600` at import (`_secure_env_permissions`).
- Key **properties**: `finnhub_keys` (non-empty keys 01–10), `has_database_url`,
  `has_backtest_database`, `has_news_database`, `has_upstash_redis`, `has_jwt_secret`,
  `has_email_verification`, `alerts_enabled`, `cors_origins`, `is_production`,
  `admin_email_list`.
- `KNOWN_LLM_PROVIDERS = ("ZAI","ZENMUX","GEMINI","GROQ")`.
- `validate_config()` → list of warnings (missing Finnhub key, missing DATABASE_URL, missing
  backtest URL, Redis enabled without creds, job monitor without DB, template gaps).
- `validate_env_template()` — every Settings field + per-provider vars must appear in
  `.env.example` (active or commented `# KEY=` lines count). **Adding a setting without
  documenting it = config validation failure.**

### `src/api.py` — the entire HTTP surface

**Middleware stack (outermost → innermost):** `MetricsMiddleware` → `RateLimitMiddleware` →
`CORSMiddleware` → routes.

**Rate limiting:** `RATE_LIMITS` (per-IP scope caps), `GLOBAL_LIMITS` (all-users shared
buckets), `SCOPE_GLOBAL` (which scope feeds which global bucket). `_scope_for_path()` maps
paths to buckets; `/auth/register` is matched before `/auth` so it lands in its own tighter
bucket. The middleware runs one `check_many()` batch per request against the shared store
(Upstash Redis when configured, in-memory otherwise; fail-open on Redis outage). 429 responses
carry `Retry-After` header **and** `retry_after` body field + CORS headers so browsers can read
them. Allowed responses carry `ratelimit-limit/remaining/reset` headers. Details in §10.

**Pydantic models** (contract surface — the frontend mirrors these in `types.ts`):
`ScreenRequest/Response`, `StockResult`, `BacktestRequest`, `BacktestResultItem`,
`BacktestResponse`, `EnrichRequest/Response`, `NewsFeedResponse`, `SentimentSummary`,
`RegisterRequest/LoginRequest/UserOut/AuthResponse`, `VerifyEmailRequest`,
`AdminUsersResponse/AdminUserUpdate/AdminOverviewResponse`, `PersonalDataPayload`.

**Startup (`on_startup`)**: validates config, self-heals the main-DB schema via
`PostgresDB().init_schema()` (idempotent — fixes "relation does not exist" on live DBs), and
marks orphaned async backtest jobs as `interrupted`.

**Route table** (see also §12):

| Method | Path | Auth | Handler |
|---|---|---|---|
| GET | `/health` | — | real `SELECT 1` DB probe + latency |
| GET | `/config` | — | config status + warnings |
| GET | `/metrics` | — | Prometheus text |
| GET | `/` | — | API identity |
| GET | `/meta` | — | capability manifest (endpoint catalog, sortable cols, filter fields, rate limits) |
| GET | `/meta/sectors`, `/meta/industries` | — | distinct values |
| POST | `/auth/register` | — | create account; returns JWT + `email_sent`; applies `ADMIN_EMAILS` bootstrap |
| POST | `/auth/login` | — | JWT; timing-equalized; unverified blocked if `EMAIL_VERIFICATION_REQUIRED` |
| GET | `/auth/me` | Bearer | current user |
| GET/PUT | `/me/personal` | Bearer | per-user watchlist/screeners/backtests (JSONB) |
| GET | `/me/entitlements` | Bearer | Day 12: plan in force, source (manual/grandfathered), limits, this month's usage, pending change |
| GET/POST | `/me/strategies` | Bearer | Day 10: list / follow a finished job (rules + symbols from the server's stored result; cap 20) |
| DELETE | `/me/strategies/{strategy_id}` | Bearer | stop following (owner-scoped WHERE) |
| GET/PUT | `/me/email-preferences` | Bearer | `weekly_digest` on/off (+ `email_verified`) |
| GET/POST | `/email/unsubscribe` | signed `unsub` token qs | GET = confirm page only (scanners prefetch); POST = off at once (RFC 8058 one-click) |
| GET | `/auth/verify-email` | token qs | flips `email_verified` |
| POST | `/auth/verify-email` | body token | same via JSON body |
| POST | `/auth/resend-verification` | Bearer | re-send email |
| POST | `/auth/logout-all` | Bearer | bump `token_version` → every token 401s |
| POST | `/auth/password-reset/request` | — | email a single-use link; identical response + timing for unknown addresses |
| POST | `/auth/password-reset/confirm` | — | set password, verify email, revoke all sessions |
| POST | `/auth/change-password` | Bearer | revoke other sessions; returns a fresh token (wrong current pw = 400, never 401) |
| POST | `/auth/refresh` | Bearer | sliding session: same `ver`, `auth_time` carried; refused past `SESSION_MAX_AGE_DAYS` |
| GET | `/auth/google/start` | — | "Continue with Google": 302 to Google's consent screen + signed `g_oauth` state cookie (`src/google_oauth.py`) |
| GET | `/auth/google/callback` | — | server-side code exchange → ID-token claims → the merge rule → 302 to `/auth/google/complete#token=…&expires_in=…`; failures redirect to `/login?oauth_error=<code>` |
| POST | `/auth/delete-account` | Bearer | password re-entry; deletes user + `user_data` (cascade) + `backtest_jobs` (no FK — explicit) in one tx |
| POST | `/screen` | — | NL query → ranked, paginated stocks (60s cache) |
| POST | `/interpret` | — | debug: NL query → filters only |
| POST | `/events` | — | browser event ingest (`src/analytics.py`); client allowlist, **400** for a server-only name; always 204 |
| POST | `/backtest` | Bearer + `gate("backtests")` | full backtest (20-min frontend timeout); charges one monthly backtest |
| POST | `/backtest/async` | Bearer + `gate("backtests")` | start job → `job_id`; charges one monthly backtest |
| GET | `/backtest/async/{job_id}` | Bearer | poll progress/result |
| POST | `/try/session` | — | mint a signed trial token (`typ:"anon"`, 30 days) — Day 6 no-signup trial |
| POST | `/try/backtest` | `X-Anon-Token` | same async job flow, owner `anon:<id>`; ≤5 symbols; 3 free runs then 403 |
| GET | `/try/backtest/{job_id}` | `X-Anon-Token` | poll a trial job (404 unless owned) |
| POST | `/try/claim` | Bearer + `X-Anon-Token` | move the trial's jobs + share links into the account |
| POST | `/results/share` | Bearer or `X-Anon-Token` | publish a finished job as a public link (server-built whitelist summary) |
| DELETE | `/results/share/{share_id}` | Bearer or `X-Anon-Token` | revoke (row deleted; owner-scoped WHERE) |
| GET | `/results/shares` | Bearer or `X-Anon-Token` | my live links |
| GET | `/r/{share_id}`, `/r/{share_id}.json`, `/r/{share_id}.png` | — | public share page (server-rendered, noindex, script-free CSP, OG/Twitter card) / JSON / 1200×630 preview card |
| POST | `/explain` | Bearer or `X-Anon-Token` | Day 11: plain-English reading of an owned finished job — deterministic `findings` + a guarded LLM `narrative` (null when down/withheld); cached by result hash |
| POST | `/backtest/export` | Bearer + `gate("csv_export")` | trades → CSV (Pro) |
| POST | `/screen/export` | Bearer + `gate("csv_export")` | screen results → CSV (Pro; was public before Day 12) |
| POST | `/enrich` | — | screen + sentiment |
| GET | `/news` | — | news feed |
| GET | `/news/{symbol}` | — | per-stock news + sentiment |
| GET | `/stocks` | — | browse universe (paginated) |
| GET | `/stocks/{symbol}` | — | single stock + sentiment |
| POST | `/refresh` | admin | trigger a refresh cycle (admin-gated) |
| GET | `/ops/jobs`, `/ops/jobs/{name}` | admin | job monitor (`require_admin` — NOT public) |
| GET | `/admin/overview`, `/admin/users`, `/admin/jobs`, `/admin/events`, `/admin/errors` | admin | admin API (`/admin/events` = funnel read-out, `/admin/errors` = scrubbed `error_log`) |
| PATCH | `/admin/users/{user_id}` | admin | update plan (via the billing provider: `plan_change` `now` default / `scheduled`), admin, verified |
| GET | `/admin` | admin | self-contained HTML dashboard (`admin_dashboard.py`) |

### `src/interpreter.py` — NL → structured filters

- `InterpretedQuery` dataclass: `filters` (list of `FilterCriterion{field, operator, value}`),
  `sector`, `industry`, `momentum`, derived booleans (`above_sma_50`, `golden_cross`,
  `near_52w_high`, `macd_bullish`, `bollinger_*`…), and `error`.
- `DERIVED_CRITERIA_KEYS` — single source of truth for the derived booleans (shared by the
  filter converter, the extraction logic and `has_derived_criteria`).
- `interpret_query(query)`:
  1. Normalize key (case/whitespace) → check 24h `_interpret_cache` (returns a **deep copy** —
     callers must never mutate the shared entry).
  2. **Hybrid path:** if `use_deterministic_interpreter` and the deterministic parse scores
     ≥ `CONFIDENCE_THRESHOLD` (0.6), return it (sub-ms, $0). Else…
  3. LLM via `get_router().chat_completion(...)` with `temperature=0.1, max_tokens=2000`
     (2000 is important — reasoning models spend ~300–400 tokens thinking; 300 truncated them).
  4. Parse JSON (strips markdown fences), convert to criteria via `field_mapping`, extract
     metadata + strict booleans only, cache, return.
  - On LLM failure it returns an `InterpretedQuery` with `error` set — **not** an empty
    universe. The screener aborts when `query.error` is set.
- The SYSTEM_PROMPT encodes conventions (small cap = 0.3–2.0B, undervalued = PE ≤ 15, etc.).

### `src/deterministic_interpreter.py` — the $0 fast path

- Regex/dictionary parser producing the **same** `InterpretedQuery` the LLM produces.
- `SECTOR_ALIASES`, `INDUSTRY_ALIASES`, `MARKET_CAP_PRESETS`, `SYNONYM_MAP`
  ("undervalued" → max_pe 15 + max_ps 2, "oversold" → max_rsi 35, …), `NUMERIC_PATTERNS`
  (RSI/P/E/market cap/price/yield/beta/EPS/volume/P-S/P-B/EV-EBITDA/ROE/ROA/margins/growth),
  `DERIVED_BOOLEAN_PATTERNS`, `MOMENTUM_BULLISH/BEARISH`.
- `parse(query) -> (InterpretedQuery, confidence)`. Confidence: strongest component ≥ 0.6
  base, boosted by component count, penalized ×0.4 when fuzzy words ("good", "best",
  "recommend") appear with ≤2 concrete criteria.
- `_dedup_filters` merges same-field+same-op criteria keeping the stricter value.

### `src/llm_router.py` — never let the AI fail

- `ProviderEndpoint{name, base_url, model, keys[], timeout}`; providers built from
  `LLM_PROVIDERS` priority list + `<NAME>_BASE_URL/_MODEL/_KEYS` env vars; legacy fallback to
  `LLM_BASE_URL`/`LLM_MODEL`/`SCREENER_LLM_KEY`.
- `chat_completion()`: tries providers in order; keys round-robin; on failure:
  - transient (429/5xx/timeout/network) → same-key retry (`transient_retries=1`, backoff 1.5s)
    then **15s** key cooldown;
  - hard (400/401/402/403/404) → **10 min** key cooldown;
  - all keys failed → provider cooled as a unit; next request skips it fast.
  - `prefer=` param reorders providers for this call (used by the news job to pick the fast
    provider first).
- `max_retries=0` on the OpenAI client — the router owns retries (prevents doubled latency).
- `get_router()` singleton; `reset_router()` for tests.

### `src/screener.py` — the 0-API-call filter engine

- `SORTABLE_COLUMNS` (whitelist for sort keys, incl. computed `"score"`),
  `SCORE_WEIGHTS = {value: 0.30, momentum: 0.40, quality: 0.30}`.
- **Freshness guards:** `STALE_AFTER_MINUTES = 15` (flag only), `STALE_EXCLUSION_DAYS = 7`
  (**rows older than 7 days are EXCLUDED from screening**, not just flagged — pink-sheet/
  delisted junk must never surface as live data).
- `SCORE_SORT_MAX_WINDOW = 2000` (composite-score sorting ranks in Python over ≤2000 matches).
- `RELAX_MAX_FILTERS = 2` (0-match auto-relax).
- `StockScreener.screen(query, limit, offset, sort_by, sort_desc) -> ScreeningReport`:
  1. Abort early if `query.error`.
  2. `_resolve_contradictions` — e.g. "oversold RSI ≤ X" + "price > SMA50" can't coexist;
     drop the trend condition, note it in `data_quality.contradiction_resolved`.
  3. `_build_filter_set` — translate criteria into a DB filter dict; derived criteria become
     column-to-column SQL expressions (`gt_col`/`lt_col`/frac variants) on `current_price`;
     only conditions actually enforced are echoed in `applied_filters`.
  4. `_run_screen` — SQL query (LIMIT/OFFSET, TRUE `total_matches` via count) + optional
     composite scoring/ranking.
  5. **0-match rescue:** drop least-covered relaxable filters (by live `column_coverage`)
     and re-run, up to 2; explain via `data_quality.message`.
  6. `data_quality` carries staleness/counts/coverage notes.
- `screen_from_natural_language(query, ...)` is the convenience wrapper used by the API.

### `src/backtester.py` — the simulation engine (2,570 lines)

**Constants:** `DEFAULT_INITIAL_CAPITAL = 100_000`, `DEFAULT_FEE_RATE = 0.001`,
`DEFAULT_SLIPPAGE = 0.0005`, `HISTORICAL_YEARS = 10`, `BACKTEST_PARALLEL_WORKERS = 4`,
`BENCHMARK_SYMBOL = "SPY"`, `MIN_TRADES_FOR_CONFIDENCE = 30`,
`MAX_EQUITY_CURVE_POINTS = 300`, fixed `ROBUSTNESS_SEED = 42`, `MONTE_CARLO_SIMULATIONS =
1000`, `BOOTSTRAP_SIMULATIONS = 1000`, `WALK_FORWARD_SPLITS = 4`, RSI/SMA sensitivity grids.

**Key functions:**
- `_translate_to_rules(interpreted)` — LLM filters → `{buy: [...], sell: [...]}` rule lists.
  Notable semantics: `max_rsi` → buy `rsi < X`; `min_rsi ≥ 70` or literal "overbought" → sell
  `rsi > X` (a `min_rsi=50` momentum filter must NOT become an immediate sell rule); **P/E is
  deliberately NOT translated** (no per-bar P/E column — the screener already selected the
  names; emitting a `pe` rule would silently compare price to P/E via a `Close` fallback);
  momentum emitted once after the loop as `price > sma_50` AND `sma_50 > sma_200`, with a
  default exit of `price < sma_200` (Day 9 — exclusive of the entry, so it cannot churn; a
  dip-buying RSI entry keeps `rsi > 70`); defaults `rsi < 30` buy / `rsi > 70` sell; risk params
  (stop-loss/take-profit/trailing/position size/leverage/short) extracted from plain English
  via regex (`_extract_pct`).
- `fetch_historical_data(symbol, start_date=None)` — yfinance download, normalized, **starts
  at `min(requested_start, today − 10y)`** (the start_date truncation bug fix — see §19).
  Returns `(df, cache)`. Cache: SQLite (`backtest_db_path`) in dev, `BacktestDB` (Neon) in
  prod.
- `BacktestEngine.backtest(symbol, strategy, start_date, end_date, ...)` — the sim.
  **Cache-coverage check:** if the cached OHLCV starts >7 days after the requested window,
  it refetches (the cache was populated by a shorter window). This fixed the bug where the
  persisted row said `2016-01-01` but the curve started `2016-08-03`.
- `run_backtest(...)` — the core loop: indicators (`_calculate_indicators`: RSI-14, SMA-50/200,
  MACD, Bollinger, ATR), signal evaluation (`_apply_rule`), trade execution with fees/slippage/
  volume caps, short support, stops (stop-loss/take-profit/trailing), equity curve
  (downsampled to ≤300 pts), drawdown curve, benchmark (SPY) with alpha/beta/excess,
  buy-and-hold comparison, data-quality notes, Monte Carlo (`monte_carlo_trades`),
  bootstrap CI (`bootstrap_confidence`).
- `walk_forward_analysis`, `parameter_sensitivity` — robustness tiers.
- `compute_portfolio` — combine per-symbol daily equity curves into a shared pool with
  `equal`/`risk_based` allocation + rebalance schedule.
- `BacktestEngine.backtest_top_results(interpreted, top_n, ...)` — screens the universe, then
  backtests top matches concurrently (ThreadPoolExecutor, 4 workers).
- `parse_backtest_query`, `_extract_tickers` (uppercase symbols in the query pin specific
  tickers), `_extract_date_window`.
- Results persisted to `backtest_results` (keyed by `user_query_hash`).

**Contract gotcha:** the API `/backtest` takes a **natural-language `query`** (plus `top_n`,
optional risk overrides, `start_date`/`end_date` `YYYY-MM-DD`, `symbols[]` for compare mode,
portfolio knobs). It does **not** accept `symbol`/`strategy`/`initial_capital` as request
fields — `initial_capital` is engine-fixed at $100,000. Pin a ticker by putting its uppercase
symbol in the query text.

### `src/database.py` — three PostgreSQL clients + SQLite fallback

- **Shared pools:** every client instance shares ONE module-level
  `ThreadedConnectionPool` (min=1, max=5) per database, created lazily under a lock. `close()`
  closes the shared pool and resets it (the scheduler calls `close()` after each cycle so Neon
  can suspend). `_get_shared_conn` probes each connection with `SELECT 1` at checkout and
  discards stale ones (Neon auto-suspend kills idle sockets) — the source of the "intermittent
  502s after idle" fix.
- `_sanitize_dsn` strips `channel_binding=require` from Neon URLs (breaks psycopg2 pools).
- **PostgresDB** (main DB): `stock_master` CRUD + filters, user CRUD (create/get/set_plan/
  set_admin/list/count/stats), email verification token ops, `user_data` get/upsert,
  `query_stocks`/`count_stocks`/`aggregate_stats`/`column_coverage`/`data_freshness`,
  `bulk_upsert_stocks`, `get_top_symbols`. All column names resolved against
  `STOCK_MASTER_COLUMNS` before SQL interpolation.
- **NewsDB** (news DB): `upsert_articles`, `upsert_sentiment`, `get_sentiments_for_symbols`,
  feed queries.
- **BacktestDB** (backtest DB): `historical_prices` (symbol,date unique) + `backtest_results`;
  methods `init_schema`, `insert_historical_prices`, `get_historical_prices`,
  `get_all_symbols_with_data`, `get_symbol_max_dates`, `count_prices`, `save_backtest_result`,
  `close`. (SQLiteDB mirrors this contract with an extra `connect()`.)
- **SQLiteDB** — dev/test-only fallback mirroring the same storage contract.
- `coverage_gap_columns(counts, min_pct=50)` — pure function used by the screener gate.
- Event/error surface: `insert_event` / `count_events` / `first_event_at` / `has_event` /
  `list_events` and `insert_error` / `list_errors`, plus the module-level
  `_event_identity_where(user_id, anon_id)` — the ONE definition of "this person"
  (user_id when set, else anon_id) shared with `src/analytics.py`.
- Schemas, backfills (`STOCK_MASTER_COLUMN_BACKFILL`, `USERS_EMAIL_VERIFICATION_BACKFILL`)
  and `MARKET_CAP_SANITY_SWEEP` live here as constants (see §7).

### `src/data_fetcher.py` — Finnhub pool & refresh

- `FinnhubClientPool` — round-robin over the 10 keys; `initialize_client_pool()` singleton;
  sets the SDK's default timeout from `finnhub_request_timeout`.
- `_api_call_with_retry` — retries 429s (and connection errors) with backoff.
- `fetch_us_stock_symbols(pool)` — stock-symbol universe (exchange filter — no pink sheets).
- `fetch_stock_profile`, `fetch_financial_metrics`, `fetch_bulk_profiles`,
  `fetch_stock_quote`, `fetch_bulk_quotes` (with `_AggregateRateLimiter` pacing),
  `fetch_technical_indicators` (with `_fetch_technicals_yfinance` fallback),
  `fetch_company_news`.
- `compute_indicators(df)` — RSI-14, SMA-50/200, MACD-12/26, Bollinger, ATR-14 from an OHLCV
  frame, plus `volume` (latest session) and `avg_volume` (trailing 63) — Day 14: Finnhub's
  `/quote` has no volume on this plan, so the quote record carries none.
- `build_stock_record` — normalizes a Finnhub profile+quote into a `stock_master` row with
  **OTC/ADR unit-confusion guards** (market-cap sanity rules: implied shares ≤ 50B, F-suffix
  cap ≤ $100B, absolute cap ≤ $6T).
- `refresh_price_data(pool, symbols, include_technicals, tech_symbols)` and
  `refresh_fundamental_data(pool, symbols)` — the batch refresh pipelines the scheduler calls.
  `tech_symbols` = top-N by market cap (the raw symbol list is alphabetical — slicing it
  refreshes the wrong 500).

### `src/scheduler.py` — background jobs

`DataRefreshScheduler` (see §9 for cadence + heartbeat). Uses the `schedule` library in a
blocking loop: `schedule.run_pending()` every 30s, `monitor.heartbeat()` each tick (store
throttles persistence), `_alert_if_stale()` every 600s. Runs an initial price refresh at
startup. Closes DB pools after each job.

### `src/job_monitor.py` — visibility & liveness

- `JOB_DEFINITIONS` — every tracked job with its schedule + `stale_multiplier` (3). **Must
  stay in sync with scheduler.py cadence constants** if you retune them.
- `JobMonitorStore` ABC with `PostgresJobMonitorStore` (owns its own tiny pool, closes it after
  every op so Neon can suspend; heartbeat persisted ≤ every 15 min; Decimal→float
  normalization on reads) and `InMemoryJobMonitorStore` (hermetic tests).
- `JobMonitor.run(job_name)` contextmanager — records start/success/failed (+ alert on
  failure, throttled). `summary()` → per-job latest run, consecutive failures, staleness, and
  scheduler liveness from the heartbeat (alive if heartbeat age ≤ persist_interval × 3).
- Every store call goes through `_try` — monitoring never breaks a job.

### `src/cache.py` — `TTLCache`

Lock-guarded `OrderedDict` with per-entry TTL, LRU-ish eviction, opportunistic expiry sweep.
Two instances: `_interpret_cache` (24h, in interpreter.py) and `_screen_cache` (60s, in api.py).
Return **copies** of cached values where callers can mutate (interpreted queries especially).

### `src/redis_ratelimit.py` — rate-limit store

- `UpstashRedis` — stdlib `urllib` REST client (multi-exec transactions, certifi CA).
- `RateLimitStore` ABC: `check_and_increment` (fixed window: INCR + EXPIRE-NX + TTL), 
  `check_and_increment_rolling` (sorted set), `check_many` (one atomic multi-exec for the
  whole request's checks).
- `InMemoryRateLimitStore` — thread-safe per-process fallback; **semantics must stay
  identical to Redis** (e.g. rolling window uses `>=` for pruning, inclusive bare numeric
  bounds in Redis).
- `get_rate_limit_store()` — Redis when `REDIS_RATE_LIMIT_ENABLED=true` + creds + PING ok;
  in-memory otherwise; resolution cached. `reset_rate_limit_store()` for tests.

### `src/auth.py` — zero-dep auth

- `hash_password` / `verify_password` — PBKDF2-HMAC-SHA256, 600k iterations,
  `pbkdf2_sha256$iters$salt_hex$hash_hex`; constant-time compare.
- `dummy_password_hash()` — **timing-equalization fix**: login verifies unknown emails against
  a throwaway hash so both paths burn the same ~400ms (prevents account-existence timing leak).
- `create_access_token` / `verify_access_token` — HS256 JWT via `hmac` (7-day expiry from
  `jwt_expire_hours`), constant-time signature check.
- `get_current_user` (401 on missing/expired/unknown) and `require_admin` (403 for non-admin)
  FastAPI dependencies.

### `src/emails.py` — SendGrid (stdlib)

`send_email` (POST /v3/mail/send, 202 = accepted, never raises), `generate_verification_token`
(secrets.token_urlsafe(32)), `build_verification_url` (frontend origin + token),
`send_verification_email`. Failure is non-fatal to registration; surfaced as `email_sent`.

### `src/alerts.py` — operator paging

`send_alert(title, message, level, throttle_key)` — fire-and-forget daemon thread; webhook
(Slack/Discord/ntfy-compatible) preferred, email via SendGrid fallback; throttled per key
(`ALERT_WEBHOOK_MIN_INTERVAL_SECONDS`, default 900s); silent degradation (never raises);
no-op when `alerts_enabled` is false.

### `src/analytics.py` — the ONE event entry point

`EVENTS` (event name → the ONLY prop keys it may carry), `CLIENT_EVENTS` (the five the browser
may POST), `DERIVED_EVENTS`, `track(name, *, user_id, anon_id, props)`, `recent_events(limit)`,
and `scrub(text)`. 16 event names (the plan's fifteen rows: `returned_day_2`/`returned_day_7`
are two).

`track()` never raises (the whole body is wrapped — telemetry must not break a screen or a
signup), drops unknown names with a warning, keeps only whitelisted props with scalar values
(strings ≤ 64 chars), accepts `anon_id` only when `uuid.UUID()` parses it, and is a no-op when
`settings.analytics_enabled` is False. After a successful insert it computes the derived
events for the identity (`user_id`, else `anon_id`; neither → nothing): `second_backtest` when
a `backtest_completed` makes the count exactly 2, `returned_day_2` / `returned_day_7` by UTC
**calendar** date (1 day, and 6–8 days) each at most once. `scrub()` is pure: emails →
`<email>`, `eyJ…`-shaped tokens → `<jwt>`, `Bearer …` → `Bearer <redacted>`, capped at 8,000
chars (substitutes BEFORE truncating, so a sliced email can't survive). All DB access goes
through the module-level `_db()` seam, which is what tests patch.

### `src/metrics.py` — Prometheus exporter (stdlib)

`MetricsMiddleware` (records method/route-template/status + latency histogram) + `registry`
(lock-snapshot rendering) + `render_metrics()` (appends job-health gauges, recomputed at most
every 300s so a scrape never wakes suspended Neon).

### `src/news.py` — news + sentiment pipeline

`refresh_news_batch(symbols, db, pool)` — fetch `/company-news` (dedicated `NEWS_FINNHUB_KEY`,
falls back to the shared pool), score headlines **in batches of 5 stocks per LLM call**
(`score_headlines_batch`, `prefer=news_llm_prefer` e.g. `groq` for speed), store articles
(deduped by `UNIQUE(symbol, url)`) + aggregate sentiment. On total LLM outage (`llm_ok=False`)
it **skips storing** rather than overwriting good sentiment with zeros. URL scheme check drops
non-http(s) links (XSS defense). `get_sentiment_for_screen` powers the screener's sentiment
dot with 0 API calls.

### `src/enrichment.py` — per-query enrichment (legacy-ish)

`EnrichmentEngine` — screen then fetch Finnhub `/company-news` + `/news-sentiment` per stock
(2 calls × ≤10 stocks). The persistent news pipeline in `src/news.py` supersedes this for the
scheduler; `/enrich` still uses it.

### `src/admin_dashboard.py`

`DASHBOARD_HTML` — a self-contained single-file HTML page (inline CSS + vanilla JS, zero
external dependencies) served at `GET /admin` by the `admin_dashboard` route in api.py. It is
a thin client over the admin-gated API (login → JWT in localStorage → `/admin/overview`,
`/admin/users`, `/admin/jobs`). The page itself is public so the login form can render; every
data endpoint it calls is behind `require_admin`. Admin nav link in the frontend opens
`{API_ORIGIN}/admin` in a new tab.

### `src/share_page.py` — public share links (pure, no I/O)

`build_share_summary(result)` copies named fields one by one from the engine's stored
result (portfolio figures when ≥2 symbols, else the single symbol; trade-weighted win rate;
SPY shown for a portfolio only when every symbol shares one SPY window). A new engine field
stays private until someone adds it here on purpose. `render_share_page(summary, page_url=,
app_url=, image_url=None)` returns the full HTML (`noindex`, canonical, OG/Twitter tags, inline
SVG curve, disclaimer + `/risk` link, "run this yourself" → `{APP_BASE_URL}/try?q=`); every
string is `html.escape`d and the route adds a script-free CSP.

### `src/share_card.py` — link preview cards (pure, Pillow)

`render_share_card(summary) -> bytes` draws the 1200×630 PNG served at `/r/{id}.png`: query,
headline return (Inter Bold, shrinks to ≥48px so it reads at thumbnail size), SPY's end level
as a marker (a level, never a fake path), the curve on a 2× supersampled layer, and the full
disclaimer on the image. Fonts are `stratoscan/assets/fonts/Inter-*.ttf` (OFL) — the Dockerfile
copies `assets/`; without it every card 500s. Text goes through `clean_text` (strips `Cc`/`Cf`
incl. bidi overrides, drops glyphs the font lacks) and BASIC layout (no libraqm dependence).
Output is deterministic, so `api._SHARE_CARD_CACHE` keys it by `card_key(summary)` (bump
`CARD_VERSION` when the layout changes); the DB lookup runs first, so a revoked link 404s.

### `src/etfs.py` — the curated ETF set (Day 14)

`ETFS` (45 plain-vanilla funds: broad market, sector SPDRs + SMH/XBI/KRE, style/factor,
international, bonds, real assets — **no leveraged or inverse funds**, which reset daily and
make multi-day backtests measure decay), `ETF_SYMBOLS`, `etf_seed_records()`, `with_etfs()`.
Rows carry `asset_type='etf'`, sector funds use the canonical sector names, `industry` is the
category. `PostgresDB.seed_etfs()` inserts them (from `init_schema` and `setup`) with
`updated_at` at the epoch, so they stay out of screens until the price refresh quotes them;
quotes + technicals cover them (the scheduler appends them), fundamentals do not. Screens are
**stocks only** unless the query asks for funds (`InterpretedQuery.asset_type == "etf"`, set by
`deterministic_interpreter.ETF_PATTERN` or the LLM's exact `"asset_type": "etf"`); an ETF screen
needs a price but no market cap and sorts by `avg_volume` instead of market cap
(`data_quality.sort_note`). Every result carries `asset_type`; the UI labels funds
(`components/EtfBadge.tsx`). Tests: `tests/test_etfs.py`.

### `src/strategy_templates.py` — the template library (Day 8)

`TEMPLATES` (15) with `id, title, query, idea, category, buy_rules, sell_rules`; `simulates(t)`
renders the pinned rules as a sentence; `templates_payload()` feeds `GET /meta`. Three rules,
enforced by `tests/test_strategy_templates.py`: each query parses deterministically (≥ 0.6, so
**0 LLM calls**), the engine trades exactly the pinned rules (no screen-only ideas), and it does
not churn (median hold ≥ 10 trading days on synthetic up/choppy/down markets). Technical
screens must be restricted to large caps or a sector (technicals are refreshed for the top 500
only). The frontend copy (`frontend/src/lib/templates.ts`) is checked id-for-id and
query-for-query. Live check: `python verify_templates.py` (real prices + a full DB).

### `src/digest.py` — the weekly saved-strategy email (Day 10)

`evaluate_strategy(saved, load_prices=, screen_now=, today=)` re-runs a saved strategy's stored
rules (no LLM) on ≤5 of its symbols with `run_backtest(..., signal_start_date=saved_at)` and
420 days of warm-up, so only the days since saving count; equal-weight return vs SPY over the
same days, plus which symbols still / no longer / newly screen in. Never raises: statuses
`ok` / `too_early` (< 5 days) / `no_data` / `error`. `compose_email` writes text + HTML that
say "down" when it is down. `run_digest(store, send=, evaluate=, …)` holds the guarantees:
a Postgres advisory lock (`digest_lock`, own short connection), at most one email per user per
ISO week (`digest_sends` ledger; **claim before send** — a `'sending'` row is never retried,
`'failed'` is retried ≤ `DIGEST_MAX_ATTEMPTS` 3), the opt-out re-read right before each send,
a circuit breaker after 3 consecutive send failures, a per-run cap
(`DIGEST_MAX_SENDS_PER_RUN`), per-user exception isolation, and `List-Unsubscribe` headers.
`run_weekly_digest()` wires it to Postgres/engine/SendGrid and **refuses to send without
`SHARE_BASE_URL`** (the unsubscribe link is an API route). CLI: `python -m src.main digest
[--dry-run] [--user EMAIL]`. Tests: `tests/test_weekly_digest.py` (fake transport + failure
drills).

### `src/explain.py` — explain the result, don't just return it (Day 11)

`extract_facts(result)` copies a whitelist of numbers (return, SPY, excess, alpha/beta, Sharpe,
max DD + longest-drawdown months, trade stats incl. profit factor / avg win vs loss / the two
best trades' share of net profit / exit reasons, walk-forward IS vs OOS, Monte Carlo spread,
the rules) — never the query, an email or the trade log. `findings(facts)` is deterministic and
always present: why a loser lost, and the uncomfortable ones (`concentrated`, `few_trades` < 30,
`fitted`, `fragile`, `deep_drawdown`). `explain_result(result, chat=, cache=)` asks the model
(temperature 0) for `{summary, points}` and **withholds** it unless it passes
`compliance_violations` (`COMPLIANCE_PATTERNS`, 35 regexes: should/will/recommend/expect/
predict/opportunity/upside/…) and `unsupported_numbers` (every number must be a fact; a
percentage always must; a date contributes only its year). Provider down → findings only,
`narrative_status: "unavailable"`, never an error. `result_hash` = sha256(facts +
`PROMPT_VERSION`) keys `ExplanationCache` (memory → main-DB `explanations`, first writer wins),
so one result gets one reading even across restarts; failed/withheld readings are never cached.
Tests: `tests/test_explain.py` (50 varied results, failure drill, stability); live:
`verify_explain.py` (in the release gate — real model, ≤ 30% withheld).

### `src/entitlements.py` + `src/billing.py` — tiers without a payment provider (Day 12)

`plan_for(user, now=, provider=)` is the ONLY entitlement decision: grandfathered → Pro; else the
provider's `Subscription` (`plan`, `pending_plan` at `change_at`, `grace`), with a pending change
applied lazily once `now >= change_at`. Pure on the row `get_current_user` loaded — no cache, so an
admin's change applies on the next request. `PLANS` (Free: 50 backtests/month, 3 saved
strategies, no CSV, no weekly emails; Pro: unlimited, 20, yes, yes) — mirrored in
`frontend/src/lib/plans.ts` and checked. Routes take `Depends(gate(FEATURE))` (in `api.py`), which
403s with `upgrade_required`; `_charge_backtest` is the atomic usage charge. `BillingProvider`
(`subscription`, `upgrade` now, `downgrade` at period end, `revoke` after `GRACE_DAYS`, `set_plan`
immediate) has one implementation, `ManualBillingProvider`; `tests/billing_conformance.py` is the
suite a payment adapter must also pass (see `BILLING.md` for the gaps). Grandfathering:
`_grant_grandfather` takes a slot from `app_counters` inside the signup transaction.

### `src/backfill.py` & `src/cache_warmer.py`

- `build_backfill_history(symbols, years, limit, verify)` — bulk-load yfinance OHLCV into the
  Neon backtest DB; resumable (skips fresh symbols); verifies a sample symbol's row count
  against a fresh download.
- `build_backtest_history_cache(limit)` / `warm_backtest_cache()` — pre-fetch 10y history for
  top-N by market cap (scheduler daily at 03:30).
- `backfill_volume_from_history()` (Day 14) — copies the latest volume + 63-session average from
  `historical_prices` onto existing `stock_master` rows (symbols with a bar in the last 10 days;
  UPDATE only, zero API calls). Runs after each warm (its failure never fails the warm) and as
  `python -m src.main backfill-volume`. Volume therefore covers the largest names only; a volume
  filter reports `data_quality.partial_coverage` (`screener.PARTIAL_COVERAGE_COLUMNS`).

### `src/logging_config.py`

`configure_logging(level, force)` — text (colored) or JSON format, optional file; 
`configure_uvicorn_logging()` — routes uvicorn's loggers through the same config.

---

## 7. Database schema reference

Three separate Neon projects (three `*_DATABASE_URL` env vars). All `init_schema()` calls are
idempotent (`IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS` backfills).

### Main DB (`DATABASE_URL`)

| Table | Key columns | Notes |
|---|---|---|
| `stock_master` | `symbol` PK, `company_name`, `sector`, `industry`, `market_cap`, `current_price`, `price_change_pct`, `volume`, `avg_volume`, `pe_ratio`, `eps_ttm`, `dividend_yield`, `beta`, `rsi_14`, `sma_50`, `sma_200`, `high_52w`, `low_52w`, `ps_ratio`, `pb_ratio`, `ev_ebitda`, `roe_ttm`, `roa_ttm`, `gross_margin_ttm`, `net_margin_ttm`, `revenue_growth_ttm`, `eps_growth_ttm`, `macd_12_26`, `atr_14`, `bollinger_upper/lower`, `asset_type` ('stock'/'etf'), `updated_at` | The screener universe (~5,000 US, exchange-listed stocks + the curated ETF set from `src/etfs.py`). NULL = no data; filters on those columns exclude rows without the field. `updated_at` drives staleness. 10 indexes on filter columns. |
| `users` | `id` PK, `email` UNIQUE, `password_hash`, `plan` ('free'/'pro'; 'premium' = old name of pro), `plan_pending` / `plan_change_at` / `plan_change_reason` (a period-end downgrade or a grace-period revocation), `grandfathered_pro`, `email_verified`, `verification_token`, `verification_token_expires`, `is_admin`, `weekly_digest`, `created_at` | PBKDF2 hashes only. The plan in force is `entitlements.plan_for(row)`, never the raw column. |
| `usage_counters` | (`user_id` FK CASCADE, `period` 'YYYY-MM', `kind`) PK, `count` | Day 12 metered usage (backtests). Charged atomically (`consume_usage`: `… WHERE count < limit`). |
| `app_counters` | `name` PK, `value` | Counters that never go backwards; `grandfathered_pro` = slots issued (≤ 100). |
| `job_runs` | `id`, `job_name`, `status` ('running'/'success'/'failed'/'skipped'), `started_at`, `finished_at`, `duration_ms`, `records`, `details`, `error` | Job monitor. |
| `scheduler_heartbeat` | `id` (=1) PK, `last_beat_at`, `pid`, `owner` (`host:pid`, Day 15 lease) | Single-row liveness beacon + the single-scheduler lease. |
| `backtest_jobs` | `job_id` PK, `user_email`, `query`, `status` ('queued'/'running'/'done'/'error'/'interrupted'), `phase`, `done_symbols` JSONB, `total_symbols`, `result` JSONB, `error`, timestamps | Async backtest durability; orphans marked 'interrupted' on restart. |
| `user_data` | `user_id` PK FK users ON DELETE CASCADE, `watchlist` JSONB, `saved_screeners` JSONB, `recent_backtests` JSONB, `updated_at` | Per-user saved state synced from frontend. |
| `events` | `id`, `name`, `occurred_at` TIMESTAMPTZ, `anon_id`, `user_id` FK users ON DELETE SET NULL, `props` JSONB | The product funnel (Day 4). Written ONLY by `src/analytics.track()`; the per-event prop whitelist lives in `EVENTS` there — no query text, email, symbol list or exception message is ever stored. `user_id` is SET NULL on account deletion, so deleting an account anonymises its events instead of deleting history. |
| `shared_results` | `share_id` PK (128-bit random), `job_id`, `owner_user_id` FK users ON DELETE CASCADE, `owner_anon`, `query`, `summary` JSONB, `created_at` | Day 6 public share links. `summary` is built by `share_page.build_share_summary` (a field whitelist — never the raw result). Revoke = DELETE. Purged with the account and by a Google takeover of a squatted row. |
| `saved_strategies` | `id`, `user_id` FK users ON DELETE CASCADE, `query`, `rules` JSONB (`buy_rules`/`sell_rules` the engine ran), `symbols` JSONB, `saved_return_pct`, `saved_at` | Day 10. Written only from the owner's own finished job (never client numbers). ≤ 20 per user. |
| `digest_sends` | (`user_id` FK CASCADE, `week_key` e.g. `2026-W40`) PK, `status` ('sending'/'sent'/'failed'), `attempts`, `updated_at` | The once-a-week ledger. `users.weekly_digest` (BOOLEAN, default TRUE) is the opt-out. |
| `explanations` | `result_hash` PK, `payload` JSONB (`{narrative, prompt_version}`), `created_at` | Day 11. One reading per result (first writer wins). Model prose over numbers only — no user data, no owner, no cascade. |
| `error_log` | `id`, `occurred_at` TIMESTAMPTZ, `method`, `route` (template), `error_type`, `trace` | One row per unhandled exception, written by `generic_exception_handler`. `route` is the route TEMPLATE, never the raw URL (which can carry a token); `trace` is scrubbed by `analytics.scrub()` (emails → `<email>`, JWTs → `<jwt>`, `Bearer …` → `Bearer <redacted>`, capped at 8,000 chars). No bodies, no headers. |

### Backtest DB (`BACKTEST_DATABASE_URL`) — also exists as SQLite (`data/backtest_history.db`) in dev

| Table | Key columns | Notes |
|---|---|---|
| `historical_prices` | `id`, `symbol`, `date`, `open/high/low/close` (adjusted), `raw_close` (unadjusted), `volume`, `dividends`, `stock_splits`; `UNIQUE(symbol, date)` | 10y daily OHLCV. `close` = adjusted (splits/dividends baked in) — that's what the sim trades. |
| `backtest_results` | `id`, `user_query_hash`, `strategy_params`, `symbol`, `start_date`, `end_date`, `initial_capital`, `final_value`, `total_return`, `sharpe_ratio`, `max_drawdown`, `win_rate`, `total_trades`, `created_at` | Cached backtest metrics. |

### News DB (`NEWS_DATABASE_URL`)

| Table | Key columns | Notes |
|---|---|---|
| `stock_sentiment` | `symbol` PK, `buzz`, `company_news_score`, `sector_news_score`, `article_count`, `updated_at` | Per-stock aggregate, LLM-scored. |
| `news_articles` | `id`, `symbol`, `headline`, `summary`, `source`, `url`, `published_at`, `sentiment` ('positive'/'negative'/'neutral'), `sentiment_score` (-1..1), `fetched_at`; `UNIQUE(symbol, url)` | Dedupe on refresh. |

### Migrations

Alembic under `stratoscan/alembic/` (20 versions: users, stock_master, news tables, admin
role, email verification columns, user_data, job monitor tables, screener quality columns,
stripe add/drop, then the Day 3–14 additions — token version, password reset, Google sub,
events/error_log, shared_results, saved strategies + digest, explanations, entitlements,
`asset_type`, `scheduler_heartbeat.owner`). `docker-entrypoint.sh` runs `alembic upgrade head` (retried ×5 for cold
Neon) before uvicorn. `python -m src.main setup` runs the same + backtest/news schemas.
`init_schema()` self-heals on API startup as a belt-and-suspenders measure.

---

## 8. Key end-to-end request flows

### Screen (`POST /screen`)

```
axios → /screen {query, limit, offset, sort_by, sort_desc}
  → RateLimitMiddleware (scope "screen", global "screener_llm")      [maybe 429]
  → screen_stocks()
      → _screen_cache lookup (query+limit+offset, 60s)
      → interpret_query(query)     # deterministic first, LLM fallback, 24h cache
      → StockScreener().screen_from_natural_language(...)
           # contradiction resolution → SQL filter build → query → composite
           # score → 0-match auto-relax (≤2 filters)
      → _result_to_stock / _build_screen_response (adds sentiment via NewsDB,
        staleness flags, data_quality)
  → 200 ScreenResponse (30+ metrics per stock, applied_filters echo,
    total_matches, has_more/next_offset, data_quality, message)
```

### Backtest (`POST /backtest`, Bearer)

```
→ /backtest {query, top_n, start_date, end_date, stop_loss_pct, ...}
  → RateLimit (scope "backtest", global "backtest_llm")
  → _run_backtest_pipeline:
      → interpret_query(query)
      → BacktestEngine.backtest_top_results(...)  # screen → top_n symbols
           # per symbol: fetch_historical_data (yfinance, cache-coverage
           #   check → refetch if cache starts >7d after requested window)
           # run_backtest (sim + robustness + benchmark)
      → optionally compute_portfolio (≥2 symbols)
      → persist results to backtest DB
  → BacktestResponse {strategy echo, results{symbol: {equity_curve,
    drawdown_curve, metrics, trades, monte_carlo, confidence_intervals,
    walk_forward, parameter_sensitivity, data_age}}, portfolio, message}
```

### Async backtest (`POST /backtest/async` → `GET /backtest/async/{job_id}`)

Start endpoint spawns a background thread job with a UUID, persists to `backtest_jobs`,
returns `job_id`. Poll endpoint reads the in-memory `_BACKTEST_JOBS` dict first, falls back
to the DB (durability + restart recovery). Statuses: queued → running (with `done_symbols` /
`total_symbols` / `phase`) → done (with full `result`) | error | interrupted. Stale in-memory
jobs are evicted; orphaned `running` jobs are marked `interrupted` on startup.

### Register → verify → login

```
POST /auth/register {email, password}
  → RateLimit (scope "register", global "auth_register")
  → create user (email lowercased, PBKDF2 hash, email_verified=False,
    verification token 24h TTL)
  → _bootstrap_admin_role(db, email, user_id, False)   # ADMIN_EMAILS auto-promote
  → send_verification_email (non-fatal; email_sent flag)
  → 200 AuthResponse {access_token, expires_in: 7d, user{...is_admin}, email_sent}

GET /auth/verify-email?token=...   (or POST body form)
  → look up token → check expiry → mark_email_verified (token nulled)

POST /auth/login {email, password}
  → timing-equalized PBKDF2 verify (dummy hash for unknown email)
  → if EMAIL_VERIFICATION_REQUIRED and not verified → 403 with resend hint
  → _bootstrap_admin_role (idempotent)
  → 200 AuthResponse

GET /auth/me (Bearer) → UserOut (incl. is_admin, email_verified)
```

### Scheduler cycle (see §9)

### News refresh cycle

`refresh_news_batch`: fetch (dedicated key) → LLM-score batches of 5 → store (skip on total
LLM outage) → close news pool.

---

## 9. The scheduler, job monitor & watchdog

### Jobs & cadence

| Job | Schedule | Work |
|---|---|---|
| `price_refresh` | every 15 min | quotes+volume for whole universe (Finnhub, 10-key round-robin); technicals ride every 4th cycle |
| `technical_refresh` | every 4th price cycle (~60 min) | RSI/SMA/MACD/Bollinger/ATR for top-500 by market cap |
| `fundamental_refresh` | daily 02:00 | profiles: sector, market cap, PE, beta… |
| `news_refresh` | every `NEWS_REFRESH_MINUTES` (default 120) | top-150 by market cap, news + LLM sentiment |
| `backtest_cache_warm` | daily `BACKTEST_CACHE_WARMER_TIME` (03:30) | pre-fetch 10y OHLCV for top-100 |
| `weekly_digest` | daily `DIGEST_TIME` (14:00) when `DIGEST_ENABLED` | saved-strategy email; ≤1 per user per ISO week, so daily = tomorrow retries today's outage |

Each run is recorded via `JobMonitor.run(...)` → `job_runs` table. After each cycle the DB
pools are **closed** so Neon can auto-suspend (~50h/month compute, inside the free tier).

### Heartbeat & staleness

- Scheduler loop ticks every 30s and calls `monitor.heartbeat()`; the store persists a row to
  `scheduler_heartbeat` at most every `JOB_HEARTBEAT_PERSIST_MINUTES` (15).
- `scheduler alive` = heartbeat age ≤ persist_interval × 3 (~45 min).
- Each job is `stale` when its last success is older than `interval × stale_multiplier` (3×).
- `_alert_if_stale()` (checked every 600s in the loop) fires a throttled webhook/email alert
  when any job is stale or the scheduler is dead. `JobMonitor.run` also alerts on failed jobs.
- **Watchdog model (Day 15):** `fly.toml` runs two process groups from one image — `app`
  (`./docker-entrypoint.sh api`: migrations, then uvicorn; `/health` check) and `scheduler`
  (`./docker-entrypoint.sh scheduler`) — so Fly restarts either on crash. Before Day 15 there
  was no scheduler process on Fly at all. A dead scheduler is still caught by the heartbeat
  alert (~45 min).
- **Single-scheduler lease (Day 15):** `JobMonitor.claim_scheduler()` runs before any job.
  Owner = `host:pid` (`job_monitor.scheduler_owner`). Granted when free, already ours (a
  restarted container keeps hostname + pid 1), stale (last beat > 3× persist interval) or
  forced (`SCHEDULER_TAKEOVER=true` / `scheduler --takeover`); otherwise
  `SchedulerAlreadyRunning` → exit 3 + "Second scheduler refused" alert. Heartbeat writes only
  land while we hold the lease; losing it stops the loop. SIGTERM/SIGINT → clean stop →
  `release_scheduler()`, so a replacement starts at once. An UNCLEAN stop leaves the lease held
  for up to 45 min — a replacement with a new hostname is refused until then unless taken
  over (seen in the Day 15 rehearsal). Store unreachable → start anyway, marked unverified.

### `GET /ops/jobs` & `/ops/jobs/{job_name}`

Live status: per-job last run (status/timestamps/duration/records/error), consecutive
failures, staleness + reason; scheduler liveness + heartbeat age + PID.

---

## 10. Rate limiting design

**Two layers, enforced in one middleware pass:**

1. **Per-IP scope caps** (fixed 60s burst, fixed 24h daily, rolling 7d weekly):

   | Scope | burst/min | daily | weekly | Endpoints |
   |---|---|---|---|---|
   | `read` | 60 (env `RATE_LIMIT_PER_MINUTE`) | 1,000 (env `RATE_LIMIT_PER_DAY`) | 5,000 (5× daily) | everything not scoped below |
   | `screen` | 5 | 50 | 250 | `/screen` |
   | `interpret` | 10 | 100 | 500 | `/interpret` |
   | `enrich` | 3 | 20 | 100 | `/enrich` |
   | `backtest` | 2 | 10 | 50 | `/backtest`, `/backtest/async` (start), `/backtest/export` |
   | `backtest_poll` | 120 | 10,000 | 50,000 | `GET /backtest/async/{job_id}`, `GET /try/backtest/{job_id}` — free status reads; no global bucket |
   | `anon_backtest` | 2 | 6 | 20 | `POST /try/backtest` (feeds `backtest_llm`); daily cap > the 3-run allowance so the friendly 403 comes first |
   | `share` | 10 | 100 | 400 | `/results/*` (DB writes only); `/r/*` public pages fall in `read` |
   | `explain` | 5 | 40 | 200 | `/explain` (feeds its own global `explain_llm`, never the screener's) |

   These are per-IP cost shields; the per-ACCOUNT monthly backtest allowance (Day 12) is an
   entitlement (`src/entitlements.PLANS`), counted in `usage_counters`, not a rate limit.
   | `refresh` | 1 | 10 | 50 | `/refresh` |
   | `auth` | 10 | 50 | 350 | `/auth/*` except register and the email-sending routes |
   | `register` | 3 | 10 | 30 | `/auth/register` (SendGrid cost shield) |
   | `auth_mail` | 3 | 10 | 30 | `/auth/password-reset/*`, `/auth/resend-verification` (send email; share `auth_register` global) |

2. **Global buckets** (all users share; fixed 24h): `screener_llm` 900/day (screen+interpret+
   enrich), `backtest_llm` 200/day, `explain_llm` 300/day, `refresh_ops` 3/day (each refresh fires thousands of
   Finnhub calls), `auth_register` 50/day (planet-wide cap on email-sending auth actions — register, reset, resend — protecting SendGrid).

**Behavior:**
- `RATE_LIMIT_ENABLED` master switch; `RATE_LIMIT_BYPASS_IPS` comma-separated whitelist.
- Store: Upstash Redis when `REDIS_RATE_LIMIT_ENABLED=true` + creds + PING ok; in-memory
  otherwise; runtime Redis failure → fail-open to in-memory (logged).
- CORS preflight (`OPTIONS`) never counts against budget.
- 429 body: `{error: true, detail, status_code: 429, retry_after}` + `Retry-After` header +
  `Access-Control-Allow-Origin/Credentials/Expose-Headers` when the origin is allowed.
- 200 responses carry `ratelimit-limit/remaining/reset`.
- **Client IP:** `_client_ip()` — the socket peer, unless `CLIENT_IP_HEADER` names a header a
  trusted proxy sets AND overwrites (`Fly-Client-IP`, set in `fly.toml`). Without it, behind Fly
  every visitor shared the proxy's bucket. `X-Forwarded-For`/`Forwarded`/`X-Real-IP` are never
  read (clients forge them; `tests/test_adversarial.py` proves it).
- `_canonical_ip` collapses IPv4-mapped IPv6 (`::ffff:127.0.0.1` → `127.0.0.1`) so variants
  share one counter.
- Frontend: `store/rateLimit.ts` holds the 429 countdown (last-wait-wins, auto-retry on
  expiry), shared across all submit surfaces.

---

## 11. Frontend architecture

### State (Zustand stores)

| Store | State | Notes |
|---|---|---|
| `store/auth.ts` | `user`, `token`, `bootstrapped` | `login/register/logout/bootstrap`; persists token+expiry+user to localStorage; wires the axios 401 auto-logout handler; multi-tab `storage`-event sync; `bootstrap()` re-validates with `/auth/me` (skips when expiry passed). |
| `store/personal.ts` | `watchlist[]`, `savedScreeners[]`, `recentBacktests[]` | zustand `persist` (localStorage) + server sync via `GET/PUT /me/personal` (debounced 800ms push, guarded by demo mode + token). **Seeds demo data** (watchlist NVDA/META/LLY/GE + 2 sample screeners) — a UX quirk: new accounts can't tell seeds from their own saves. |
| `store/rateLimit.ts` | `pending`, `retryUntil`, `onReady` | 429 gate with live countdown + auto-retry callback. |
| `store/notifications.ts` | toasts | glass-strong toasts, bottom-right, `aria-live`. |

### API layer (`lib/api/`)

- `client.ts` — axios instance: `API_URL` = `VITE_API_URL ?? '/api'` (dev proxy),
  `ADMIN_DASHBOARD_URL` (API origin + `/admin`), `DEMO_MODE` flag, `TOKEN_KEY`,
  request interceptor (Bearer), response interceptor (401 auto-logout except on auth paths),
  `toApiError()` normalization (network errors, HTML bodies, Pydantic 422 arrays → readable
  message; `Retry-After` header or body `retry_after` → `retryAfter`), `unwrap()`.
  `LONG_TIMEOUT_MS = 20 min` for backtest calls (cold-cache yfinance downloads).
- `services.ts` — typed functions per endpoint; **zod runtime validation** (`validate()`) on
  real-API payloads to catch contract drift; demo-mode branches return synthetic data with
  simulated latency.
- `types.ts` — TS mirror of the backend contract (screen/backtest/auth/news/stocks + error).
- `schemas.ts` — zod schemas for the same shapes.
- `demo.ts` — the full demo universe/fakers (deterministic synthetic data, labeled "Demo
  data" in the UI).

### Routing (`App.tsx`)

Public: `/`, `/how-it-works`, `/features`, `/about`, `/pricing`, `/contact`, `/terms`,
`/privacy`, `/risk`, `/verification` (Day 14), `/strategies`, `/try`, `/login`, `/signup`,
`/verify-email`, `/404`.
Protected (`ProtectedRoute` + `AppShell`): `/app` (Dashboard), `/app/screener`,
`/app/backtest`, `/app/stocks/:symbol`, `/app/news`, `/app/settings`.
All pages are lazy-loaded (route-level code splitting). Global: `SkipToContent`, `Seo`,
`ScrollToTop`, `KeyboardShortcuts` (Cmd/Ctrl+K search, Cmd/Ctrl+/ help, Esc), `Toaster`,
Sentry `ErrorBoundary`.

### Pages

- **Screener** — NL query input, preset chips, results table with sort/filter/pagination,
  rank badges, RSI bars, composite score pills, sentiment dots, DataAgeBadge for stale rows,
  CSV export.
- **Backtest** (the biggest page, 1,100+ lines) — 3-pane workspace: strategy chat →
  KPI cards (total return, Sharpe, max DD, win rate vs SPY) → equity curve + drawdown charts
  (Recharts) → robustness blocks (Monte Carlo, CI, walk-forward, parameter grid) → generated
  compare mode (pin strategy A, re-run B on A's symbols) → async progress → tune panel
  (RSI thresholds/stops/position sizing/allocation) → shareable links (`lib/backtestParams.ts`
  serializes all params into the URL). Day 9: everything past the result sits in three
  collapsed, remembered layers (rules → adjust & compare → code); see §19.
- **News** — feed with per-article sentiment badges.
- **Dashboard** — watchlist, saved screeners, recent backtests (from personal store).
- **Auth / VerifyEmail / Settings / StockDetail** — auth forms, verification status,
  account settings, stock detail with chart + news.
- **Landing + marketing pages** — terminal-flavored hero (animated equity lines, ticker
  tape), features, pricing (Free vs Pro limits from `lib/plans.ts`; no price unless the price flag
  is on — `lib/pricing.ts`), how-it-works, legal.

### Design system

Frosted-glass dark theme: ground `#0a0a0f`, neon cyan `#00d4ff`, grape `#7c3aed`, gains
`#16c784`, losses `#ea3943`; Inter + JetBrains Mono (tabular numerals); Radix primitives +
CVA; `prefers-reduced-motion` respected; full detail in `DESIGN.md`.

### Demo mode

`VITE_DEMO_MODE=true` → every service function serves deterministic synthetic data through the
same typed interface; demo surfaces are labeled; production builds never ship fake data unless
the flag is set.

---

## 12. API contract essentials

- **Error envelope** (via `http_exception_handler` / `generic_exception_handler`):
  `{error: true, detail: "...", status_code: N}` (+ `retry_after` on 429). Pydantic 422s are
  NOT normalized — the frontend joins the `detail` array. `generic_exception_handler` ALSO
  writes one scrubbed `error_log` row (route TEMPLATE, exception type, redacted trace) — the
  client's body is byte-for-byte unchanged, and a failed write leaves it unchanged too.
- **Backtest is `query`-driven** (see §6 backtester gotcha). `symbol`, `strategy`,
  `initial_capital` are not request fields.
- **Auth:** Bearer JWT (7-day TTL). `require_admin` gates `/admin/*` + `/refresh`.
- **Pagination:** `limit` (1–500), `offset`; responses carry `total_matches`, `has_more`,
  `next_offset`.
- **Staleness:** results carry `is_stale` + `data_age_minutes`; rows >7 days old are excluded
  from screens entirely.
- **`/meta`** returns the capability manifest the frontend renders from (endpoint catalog,
  sortable columns, NL filter fields + operators, example queries, rate-limit budgets,
  pagination contract, auth feature flags). `test_frontend_meta.py` pins the `/meta`
  manifest itself; `test_docs_contract.py` pins the committed `openapi.json` /
  `openapi.pretty.json` snapshots against the live app (regenerate with
  `python scripts/regen_openapi.py`). `FRONTEND_API.md` is maintained by hand — when it
  and the server disagree, the server wins.
- **CSV exports:** `POST /backtest/export`, `POST /screen/export` return Blobs.
- **Health:** `/health` does a real `SELECT 1` with `db_connect_timeout` (15s — Neon cold
  starts exceed 3s; a tight probe would false-alarm).
- Full annotated contract: `stratoscan/FRONTEND_API.md`; generated snapshots
  `stratoscan/openapi.json` / `openapi.pretty.json`.

---

## 13. Configuration & environment variables

All keys in `stratoscan/.env` (gitignored, chmod 600). Full annotated template:
`stratoscan/.env.example` (must stay in sync — validated at boot). Groups:

| Group | Vars | Notes |
|---|---|---|
| Databases | `DATABASE_URL`, `BACKTEST_DATABASE_URL`, `NEWS_DATABASE_URL` | 3 Neon projects; backtest falls back to SQLite when unset (dev/tests only) |
| Finnhub | `FINNHUB_KEY_01..10`, `NEWS_FINNHUB_KEY`, `FINNHUB_REQUEST_TIMEOUT` | round-robin pool |
| LLM | `SCREENER_LLM_KEY`, `BACKTEST_LLM_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_PROVIDERS`, `LLM_PROVIDER_TIMEOUT`, per-provider `<NAME>_BASE_URL/_MODEL/_KEYS/_TIMEOUT` (ZAI/GEMINI/GROQ/ZENMUX) | `LLM_PROVIDERS` priority list; free flash models |
| Auth | `JWT_SECRET`, `JWT_EXPIRE_HOURS`, `PASSWORD_MIN_LENGTH` | `openssl rand -hex 32` |
| Email / links | `SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS`, `APP_BASE_URL`, `SHARE_BASE_URL` (public API origin for `/r/{id}` links — request origin when unset — and for digest unsubscribe links, where unset = **no digest is sent**), `EMAIL_VERIFICATION_REQUIRED`, `EMAIL_VERIFICATION_TOKEN_TTL_HOURS` | sender must be verified in SendGrid |
| Admin | `ADMIN_EMAILS` | auto-promoted at register/login |
| Rate limits | `RATE_LIMIT_ENABLED`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_DAY`, `RATE_LIMIT_BYPASS_IPS`, `CLIENT_IP_HEADER` (`Fly-Client-IP` on Fly — the real visitor IP; blank = socket peer), `REDIS_RATE_LIMIT_ENABLED`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `REDIS_RATE_LIMIT_TIMEOUT` | read bucket env-configurable; cost shields fixed |
| Caching | `CACHE_ENABLED`, `INTERPRET_CACHE_TTL`, `INTERPRET_CACHE_MAX_ENTRIES`, `SCREEN_CACHE_TTL`, `SCREEN_CACHE_MAX_ENTRIES` | |
| CORS | `CORS_ALLOW_ORIGINS` | comma-separated; locked down (no wildcard) |
| Scheduler/monitor | `JOB_MONITOR_ENABLED`, `JOB_HEARTBEAT_PERSIST_MINUTES`, `BACKTEST_CACHE_WARMER_*`, `NEWS_SYMBOLS_LIMIT`, `NEWS_REFRESH_MINUTES`, `NEWS_LLM_PREFER`, `DIGEST_ENABLED`, `DIGEST_TIME`, `DIGEST_MAX_SENDS_PER_RUN` (60 — SendGrid's free ~100/day is shared with verification/reset mail) | |
| Alerting | `ALERT_WEBHOOK_URL`, `ALERT_EMAIL`, `ALERT_WEBHOOK_MIN_INTERVAL_SECONDS` | at least one channel or alerts are no-op |
| Ops | `APP_ENV` (production disables docs), `LOG_LEVEL`, `LOG_FORMAT`, `METRICS_ENABLED`, `DB_CONNECT_TIMEOUT`, `YFINANCE_TIMEOUT`, `USE_DETERMINISTIC_INTERPRETER` | |

**Staging:** `staging.env` + `staging.env.example` mirror production with staging resources
(`STAGING_DATABASE_URL` → isolated `stratoscan_probes` DB; `RATE_LIMIT_ENABLED=false` on the
staging API by design for E2E). See `STAGING.md`.

---

## 14. Testing & verification

### Backend hermetic suites (`tests/test_*.py`, no network/quota)

```bash
cd stratoscan
python -m tests.test_imports          # import smoke
python -m tests.test_backtest_golden  # frozen numbers — engine math change fails here
python -m tests.test_backtester       # engine unit tests
# ... any of tests/test_*.py
# run them all:
for t in tests/test_*.py; do python -m "tests.$(basename "$t" .py)" || echo "FAIL $t"; done
pytest                               # also works (pytest config in pyproject.toml)
```

Key suites: `test_public_claims` (Day 14 — public copy vs code: bans removed claims, checks
Monte Carlo / walk-forward / fees / free allowance / ETF count / the split example / the accuracy
line / the drill count), `test_corporate_actions` (the 2-for-1 split worked example),
`test_etfs`, `test_volume_backfill`, `test_auth`, `test_admin`, `test_rate_limits`, `test_scheduler`,
`test_scheduler_watchdog`, `test_job_monitor`, `test_metrics_alerts`, `test_llm_router`,
`test_interpreter` / `test_deterministic_interpreter`, `test_screener_quality`,
`test_data_fetcher`, `test_backfill`, `test_enrichment`, `test_news`, `test_email_verification`,
`test_personal_data`, `test_api_smoke`, `test_api_dirty_data`, `test_backtest_*` (golden,
fillin, tier3, tier4, db, portfolio), `test_redis_ratelimit`, `test_cache`, `test_pagination`,
`test_docs_gating`, `test_frontend_meta`, `test_migrations`/`test_migration_behavior`,
`test_security_fixes`, `test_coverage_gate`, `test_cache_warmer`, `test_main`.

**Fakes** (`tests/fakes.py`): `FakeLLMRouter` (canned JSON, call recording),
`FakeUserDB` (in-memory users with exact method surface incl. admin + personal-data),
`FakeQualityDB` (real filter semantics over an in-memory list). Suites patch
`src.llm_router.get_router` before constructing the TestClient. CI sets a dummy `JWT_SECRET`
(boot guard) — auth endpoints 503 without it.

### Live behavior probes (`verify_*.py`)

Ground-truth checks that run against real staging resources. Notable: `verify_backtest_accuracy`
(hand-computed fills/PnL/Sharpe/DD/benchmark), `verify_trade_pnl` (cash P&L vs reported),
`verify_failure_drills` (dead keys/LLM/yfinance/Upstash), `verify_throughput` (load + 429s +
headers), `verify_data_accuracy` (25-symbol Finnhub-vs-yfinance diff),
`verify_scheduler_watchdog`, `verify_upstash_ratelimit`, `verify_llm_router_lifecycle`,
`verify_screener_lifecycle`, `verify_backtest_neon_lifecycle`, `verify_security_findings`,
`verify_full_backend_behavior`, `verify_audit_behavior`, `verify_load_behavior`,
`verify_screen_cache_live`, `verify_derived_fix`, `verify_backtest_edge_paths`,
`verify_security_fixes_behavior`, `verify_coverage_gate`, `verify_fixes_implemented`.

### Release gate

`run_probes.py` runs its `PROBES` list sequentially in subprocesses, each with env staged from
`staging.env` and `DATABASE_URL` forced to the isolated `stratoscan_probes` DB (Upstash
blanked; `verify_throughput` gets its own booted uvicorn with rate limiting ON). Exit 0 = all
pass; writes a markdown report. `scripts/run_release_gate.sh` wraps it. CI gate job runs all
`tests/test_*.py` + `py_compile`; optional release-gate job runs the probes against staging.

### Accuracy loops

- `accuracy_loop.py` — 30 diverse queries, independently recomputes every reported number
  (per-trade PnL vs cash deltas, cash conservation `final_value == initial + ΣPnL`, win rate,
  endpoints, benchmark/excess, strategy echo, garbage fails honestly). Last run 2026-10-05:
  3,503/3,503 checks on 49 symbol results (offline stack — 10 model-dependent queries not run;
  `ACC_ALLOW_UNRUN=1`). Queries that return no result are reported as NOT RUN and fail the loop
  unless allowed — the pre-Day-13 counter double-counted and hid them.
- `rule_execution_loop.py` — ground-truth rule execution tests.

### Adversarial, drills, end-to-end, accessibility, load (Day 13)

- `tests/test_adversarial.py` — every guarded route (found from the app) × no token / tampered /
  alg=none / expired / deleted-account / purpose tokens; IDOR; privilege escalation; injection
  through the SQL writer AND the interpreter; XSS on share/unsubscribe/admin; rate-limit bypass
  (forged headers, token cycling, preflight, rolling edge identical in Redis and memory).
- `tests/test_failure_drills.py` — drills 5–7 (mail down, error capture failing, DB unreachable
  mid-request → 503 + Retry-After, never 500, never a logout). Drills 1–4: `verify_failure_drills.py`
  (also hermetic; in the release gate). Day 15 fixed drill 2: it now forces the LLM path and
  authenticates `/backtest` — before, the free parser and a 401 made it pass without testing anything.
- `tests/test_db_pool.py` — the shared pool waits for a connection instead of failing.
- **E2E** (`frontend/e2e/`, `npx playwright test`; CI: `e2e.yml` on every push) — four journeys
  plus an axe-core + keyboard sweep, against `stratoscan/e2e_stack.py`: a throwaway local Postgres
  (`stratoscan_e2e` / `stratoscan_e2e_bt`, dropped every run) seeded with 12 large caps and 10y of
  SYNTHETIC prices, an SMTP sink that writes decoded mail to `data/e2e_mail.jsonl` (tests read
  verification/reset links from it), verification required, rate limiting off, no LLM.
- **Load** — `python load_test.py` against the E2E stack; results and the free-tier ceiling in
  `stratoscan/LOAD.md`.

### Frontend

```bash
cd frontend
npm run typecheck      # tsc -b --noEmit (strict)
npm run lint           # eslint
npm run format:check   # prettier
npm test               # vitest run (~205 tests across 26 files)
npm run build          # tsc -b && vite build
npm run verify:walkforward   # walk-forward scale guard
npm run verify:behavior      # E2E behavioral verification (node)
```

---

## 15. CI/CD & deployment

Five GitHub Actions workflows (push to `main`; frontend ones and e2e also on PRs; e2e on every push):

1. **`ci.yml`** (backend accuracy gate, on backend paths): ruff lint → frozen golden set →
   `verify_backtest_accuracy.py` → `verify_trade_pnl.py` → 6 backtester suites.
2. **`frontend-ci.yml`**: typecheck → lint → format → vitest → walk-forward guard → build.
3. **`deploy.yml`** (backend → Fly.io, `stratoscan-api`): gate (every `tests/test_*.py` suite +
   py_compile with hashed lock) → optional release-gate job (staging probes, blocks deploy on
   failure) → stage secrets (`fly secrets import --stage`, skips unset GitHub secrets so live
   Fly values are never wiped; `APP_ENV=production` set directly) → `fly deploy --remote-only`
   (entrypoint runs `alembic upgrade head` ×5 retry for cold Neon before uvicorn) →
   healthcheck gate (poll `/health` for 200, ~5 min budget) → rollback on failure. There is
   **no `fly rollback` command** (the step that called one was fixed on Day 15): each deploy's
   image is labelled `sha-<commit>`, and a rollback redeploys
   `registry.fly.io/stratoscan-api:sha-<last green commit>` (found via the Actions API).
   Manual trigger: `workflow_dispatch` with `run_release_gate` input.
4. **`e2e.yml`** (every push + PR): Postgres service container → backend + frontend deps →
   `npx playwright install --with-deps chromium` → `npx playwright test` (starts `e2e_stack.py`
   and a `vite preview` build itself); report + traces uploaded on failure.
5. **`frontend-deploy.yml`** (frontend → Vercel): gate → deploy with `VITE_API_URL` +
   `VITE_SENTRY_DSN` injection → healthcheck.

**Required GitHub secrets:** `FLY_API_TOKEN`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`,
`VERCEL_PROJECT_ID`, all backend env vars (`DATABASE_URL`, `BACKTEST_DATABASE_URL`,
`NEWS_DATABASE_URL`, `JWT_SECRET`, `FINNHUB_KEY_01..10`, `NEWS_FINNHUB_KEY`, LLM vars,
`UPSTASH_*`, `SENDGRID_*`, `APP_BASE_URL`, `CORS_ALLOW_ORIGINS`, `ADMIN_EMAILS`,
`ALERT_WEBHOOK_URL`, `LOG_*`, `SENTRY_DSN`), plus staging release-gate secrets
(`STAGING_DATABASE_URL`, staging LLM keys).

**Infra:** Fly.io `fly.toml` (two process groups — `app` + `scheduler` — one machine each, iad
region, `/health` check every 30s on `app`, 256MB VMs). Launch runbook, drills and costs:
`RUNBOOK.md`. Vercel for the frontend. `DEPLOY.md` is the manual runbook; the
workflows win when the two disagree. Frontend security headers ship via
`frontend/vercel.json` — Vercel ignores `public/_headers`, which is kept only as a
portable mirror and pinned in sync by `npm run verify:headers`.
`CORS_ALLOW_ORIGINS` must include the real frontend origin in prod.

---

## 16. Common development tasks

| Task | Command |
|---|---|
| Start API | `cd stratoscan && python run.py` (add `--no-reload` for prod-style) |
| Start scheduler | `python -m src.main scheduler` |
| Run one backend suite | `python -m tests.test_backtester` |
| Run all backend suites | loop over `tests/test_*.py` (see §14) |
| Run the release gate | `python run_probes.py [--only NAME ...] [--report out.md]` (needs staging.env + seeded probes DB via `seed_probes_db.py`) |
| Frontend dev | `cd frontend && npm run dev` |
| Frontend tests | `npm test` |
| Regenerate OpenAPI | `python scripts/regen_openapi.py` (writes both snapshots; `test_docs_contract` fails if stale) |
| Migrations | `alembic upgrade head` (auto-run at boot; `python -m src.main setup` too) |
| Backfill OHLCV | `python -m src.main backfill-history --symbols AAPL,MSFT --years 10` |
| Warm cache once | `python -m src.main warm-cache` |
| Live server on staging | `python launch_staging_detached.py` (API on :8010) |
| Production-shaped local stack | `make rebuild` (repo root; compose: API + scheduler containers, 3 DBs, Redis shim, mailbox) |
| Backup/restore drill | `python scripts/backup_restore_drill.py --report drill.md` (from `stratoscan/`; needs the three `*_DATABASE_URL`) |
| Rollback drill | `scripts/rollback_drill.sh` (repo root, against `make up`) |
| Who holds the scheduler lease | `GET /ops/jobs` → `scheduler.lease_owner` |

---

## 17. Cost controls & operational model

- **Screener = 0 API calls** — everything is pre-fetched into `stock_master` by the scheduler.
- **Interpret cache 24h** + **screen cache 60s** — repeat queries cost nothing.
- **Deterministic interpreter first** — ~80% of queries never touch an LLM.
- **LLM router cooldowns** — transient failures cool 15s, hard failures 10 min; dead
  providers are skipped fast.
- **Global rate-limit buckets** — planet-wide caps on LLM usage (`screener_llm` 900/day,
  `backtest_llm` 200/day), refresh (`refresh_ops` 3/day) and registrations
  (`auth_register` 50/day) protect the free-tier budgets of every provider.
- **10 Finnhub keys** round-robin; dedicated news key so news never competes with the screener.
- **Neon auto-suspend** — pools closed between scheduler cycles; heartbeat persists ≤15 min;
  /metrics job gauges recomputed ≤ every 5 min. Target ≈50h/month compute.
- **Sentiment LLM batching** — 5 stocks per call, prefers the fast provider (Groq).
- **Alerting** — throttled (1 per key per 15 min), fire-and-forget, no-op unless configured.
- **Market-cap sanity sweep** on upsert — corrupt OTC/ADR caps nulled, never served.

---

## 18. Gotchas & landmines

- **Never run two schedulers.** Duplicate refresh jobs corrupt/duplicate data.
- **Backtest ignores `symbol`/`strategy`/`initial_capital` fields** — the API is
  query-driven; `initial_capital` is fixed at $100k.
- **`start_date` truncation:** `fetch_historical_data` starts at `min(requested, today−10y)`;
  the engine checks cache **coverage** and refetches when the cache starts >7 days after the
  requested window. Don't remove that check; don't trust cache presence as completeness.
- **`_translate_to_rules` semantics:** `min_rsi` (gte) only becomes a sell rule when ≥70 or
  the query literally says "overbought"; momentum emits ONE rule after the loop; **never
  translate P/E into a trade rule**.
- **`.env.example` is validated** — add the key when you add a setting.
- **`init_schema()` self-heals** the main DB at API startup — don't remove it; it's what makes
  a bare deploy (which never ran `setup`) work.
- **Shared DB pools + `close()`:** the scheduler's `close()` closes the pool every request may
  be using; `_get_shared_conn` handles the race — keep new DB code on that path
  (`_get_conn`/`_get_shared_conn`), never open per-instance pools.
- **Neon stale connections:** probe at checkout (`SELECT 1`) or you'll ship intermittent 502s.
- **In-memory vs Redis rate-limit store must behave identically** (rolling-window boundary
  semantics: `>=` pruning, inclusive bounds).
- **`maxconn` matters:** BacktestDB pool is small (3); parallel workers are 4; don't crank
  workers without raising the pool or you'll serialize/queue.
- **Response shapes are contracts:** update `types.ts`, `schemas.ts`, `FRONTEND_API.md` and
  the `openapi.json` snapshot together.
- **No raw exception text in responses** (security review finding) — log it, return generic.
- **`JWT_SECRET` is required** even in hermetic tests (boot guard 503s) — CI sets a dummy.
- **Entry and exit rules must be mutually exclusive** — buy and sell rules are each ANDed and
  evaluated on every bar; if the exit can be true while the entry is, the sim buys and sells on
  consecutive bars. That was momentum until Day 9 (`price > sma_50` / `rsi > 70`: median hold 1
  day). Golden scenario D and `test_strategy_templates` pin the fix.
- **Tune overrides are request fields, never query text** — `rsi_buy_below` / `rsi_sell_above`
  (plus stop/target) are applied in `_run_backtest_pipeline` and echoed in `strategy`; the
  frontend sends only what the user set. Don't reintroduce text appended to `query` — the
  parser ignores it and it shows up on share pages.
- **Never gate accuracy, and never check a plan by hand.** Gate a route only with
  `Depends(gate(FEATURE))` (tests/test_entitlements enumerates gates from the app — a new one
  without a direct-API test fails), never with `user["plan"] == …`; don't add a fifth gated
  capability without updating `entitlements.FEATURES`, `PLANS`, `lib/plans.ts` and `BILLING.md`;
  robustness, explanations and share pages stay identical on every plan.
- **The explanation must never become advice.** Don't loosen `COMPLIANCE_PATTERNS` to let a
  paragraph through, don't pass the model numbers outside `extract_facts`, and don't show a
  `withheld` narrative. Changing the prompt or the facts means bumping `PROMPT_VERSION`.
- **A database outage is a 503, never a 500 and never a 401.** `generic_exception_handler` maps
  `OperationalError`/`InterfaceError`/`PoolError` to 503 + `Retry-After`; `auth._fetch_user`
  raises 503 rather than returning None (None → 401 → the frontend signs the person out). The
  shared pool WAITS up to `POOL_CHECKOUT_WAIT_SECONDS` for a free connection (psycopg2 itself
  raises at once). The filter whitelist is enforced in `_build_filter_where` for BOTH sides —
  don't move it back to the callers.
- **Public copy is checked against code** (`tests/test_public_claims.py`). Changing Monte Carlo
  runs, walk-forward windows, fees, the free allowance, the ETF set or the split example means
  updating the pages that quote them; a figure on `/verification` must come from a test or a dated
  run — if a check has not run, the page says so.
- **Never let a digest go out with a dead unsubscribe link** — `run_weekly_digest` refuses
  without `SHARE_BASE_URL`; GET on `/email/unsubscribe` must stay side-effect free (scanners).
  A `'sending'` ledger row is never retried: a missed email beats a duplicate.
- **Scheduler cadence constants live in TWO places** — `scheduler.py` and
  `job_monitor.JOB_DEFINITIONS` — keep in sync when retuning.
- **Docs disabled in production** — verify with `APP_ENV=production`.
- **Frontend personal store seeds demo data** — brand-new accounts see a seeded watchlist/
  screeners; that's intentional (local state wins while server is empty).
- **Rate-limit UX:** 429s must honor `Retry-After`; frontend has the shared countdown gate.
- **Don't loosen cost-shield scopes** (`screen`/`enrich`/`backtest`/`refresh`/`register`)
  "because users hit them" — they're budget protection, and the global buckets are the
  backstop.
- **`staging.env` has `RATE_LIMIT_ENABLED=false` by design** (E2E/probes); never assume the
  staging API rate-limits.

---

## 19. Recent significant changes

These shaped current behavior — don't "clean up" them without understanding why:

- **Day 15 — dress rehearsal:** cold start 66 s, backup/restore of all three DBs identical,
  rollback 9.5 s (`RUNBOOK.md` has the numbers and the launch checklist). Found and fixed: two
  schedulers could run side by side (now a lease — §9), `fly.toml` had no scheduler process at
  all (now `[processes]` app + scheduler), and `deploy.yml` called `fly rollback`, which does not
  exist (now redeploys the last green `sha-<commit>` image); failure drill 2 passed without
  testing anything (fixed). Migration `f0a1b2c3d4e5` (`scheduler_heartbeat.owner`). Not done
  here: the staging release gate, real market data, the five-person sessions.

- **Day 14 — make every claim true:** volume backfilled from stored daily bars
  (`cache_warmer.backfill_volume_from_history`, `compute_indicators` volume; a volume filter reports
  `data_quality.partial_coverage`); a curated, labelled ETF set (`src/etfs.py`, `asset_type`,
  migration `e9f0a1b2c3d4`; screens stay stocks-only unless asked); the never-proven LSE history
  source deleted; `/meta` example queries that silently dropped clauses ("low debt", "high profit
  margins", "strong momentum") replaced and pinned; public `/verification` page (golden set,
  dated accuracy loop, split worked example, seven drills, what is not covered) linked from the
  landing page and footer; landing rewritten around testing ideas (signed-in search now opens the
  Backtest page); stale public claims removed (1,400+ stocks, 24/7, real-time, 10 backtests/day,
  "trains on the past", demo-only sparklines, test/line counts, broker integration) and
  `tests/test_public_claims.py` keeps them out. Risk page fill statement corrected (next-day open).

- **Day 12 — entitlements without a payment provider:** `src/entitlements.py` (`plan_for`, the
  one decision) + `src/billing.py` (`BillingProvider`, `ManualBillingProvider`) + `gate()` on the
  four gated routes + `/me/entitlements`; migration `d8e9f0a1b2c3` (plan state columns,
  `usage_counters`, `app_counters`, first-100 grandfathering backfill, `premium` → `pro`). The
  digest only emails entitled accounts. `/screen/export` now needs an account. Frontend: real plan
  card in Settings, gated export buttons, plan badge, Pricing rewritten (Free vs Pro, no prices —
  `VITE_PRICING_ENABLED`), and every "no paywall / no tiers / every feature for everyone" claim
  corrected to "the numbers are never paywalled". `BILLING.md` lists what a payment adapter needs.

- **Day 11 — explain the result:** `POST /explain` + `src/explain.py` + the Backtest page's
  "What happened" panel (`components/backtest/ExplainPanel.tsx`, under the risk disclaimer).
  Findings are deterministic and always render; the model's paragraph is shown only past the
  compliance + number guards, and its absence never blocks the result. New table
  `explanations` (migration `c7d8e9f0a1b2`), scope `explain` + global `explain_llm`, probe
  `verify_explain.py` added to the release gate (19 probes).

- **Day 10 — saved strategies + the weekly email:** "Follow weekly" on a finished backtest
  (`components/backtest/FollowButton.tsx`) saves it via `POST /me/strategies` with only the
  job id; the Dashboard's "Saved strategies" panel lists / re-runs / unfollows; Settings → Email
  (`#email`) is the one real switch (the old Notifications toggles were local state that sent
  nothing — removed). `src/digest.py` + `digest_sends` + `saved_strategies` (migration
  `b6c7d8e9f0a1`); see §6 for the send guarantees. `get_current_user` now refuses **any** token
  with a `typ` claim (account tokens have none; `anon` and `unsub` do). `send_email(...,
  headers=)` carries `List-Unsubscribe` (CR/LF headers dropped). `BacktestEngine.load_prices`
  is the cache-then-yfinance loader, extracted from `_backtest_one` for the digest's re-run.

- **Day 8 — templates + honest empty states:** 12 templates (`src/strategy_templates.py`), a
  public `/strategies` page, template pickers in the Backtest empty and zero-match states,
  landing chips that are templates (the old "momentum breakout stocks" chip sent trial users
  into a churning backtest), a "Your share links" dashboard panel, and a Screener notice whenever
  the zero-match rescue relaxes filters or a contradiction drops one. Parser: "above/below/near
  **their** …" now sets the same trend flags as "its" (plural queries silently lost the
  condition). Golden cross / Bollinger ideas are screened but traded on RSI timing, so they are
  not templates.
- **Day 9 — five layers:** the Backtest result shows layers 1–2; "Show the rules" (layer 3,
  `RulesPanel` + `lib/rules.ts` — English rules, defaults labelled, RSI thresholds editable →
  re-run with `rsi_buy_below`/`rsi_sell_above`), "Adjust & compare" (layer 4 — overfitting
  warning, compare/baseline, setup link, tuning panel) and "Show the code" (layer 5 — script +
  raw `strategy` JSON) are `Disclosure`s collapsed by default and remembered per user
  (`lib/disclosure.ts`, key `stratoscan_layers:<user:id|guest>`). The code pane is no longer a
  permanent split. `text-low` is `#8690aa` (was 4.24:1 on glass-strong); `contrast.test.ts`
  measures every text token over every panel/backdrop. Charts carry `role="img"` + a summary.
- **Day 9 (part) — momentum stops churning; Tune is real:** approved signal change — bullish
  momentum now enters on `price > sma_50 AND sma_50 > sma_200` and exits on `price < sma_200`
  (median holds 18–128 days across 6 seeds × 4 synthetic regimes, vs 1 day before). Pinned by
  golden scenario D; three momentum templates are back (15 total). The Tune panel's RSI fields
  are now `rsi_buy_below` / `rsi_sell_above` request fields; the Backtest page no longer appends
  "momentum: RSI buy below …" to the query, no longer applies a silent 8% stop / 20% target,
  and drops the SMA fast/slow and capital inputs the engine never read. A shared link's Tune
  settings now apply to the run it triggers (they were hydrated one render too late).
- **Day 6 — no signup wall + share links:** `/try/*` runs real backtests on a signed trial
  token (`auth.create_anon_token`, `typ:"anon"`; `get_current_user` refuses it, and
  `verify_anon_token` refuses account tokens). Trial jobs live in `backtest_jobs` with owner
  `anon:<id>` (no `@`, so it can never collide with an email). Three layers hold it: the
  3-run allowance (counted from memory AND the DB, so it survives a restart), the per-IP
  `anon_backtest` scope (a new token doesn't reset it), and the global `backtest_llm`.
  `/results/share` publishes `share_page.build_share_summary(result)` — a whitelist — into
  `shared_results`; `/r/{id}` renders it server-side. Also fixed: the async job stored the
  Pydantic `BacktestResponse`, which `json.dumps` cannot serialize, so the "done" upsert
  failed silently and finished results never reached `backtest_jobs` (lost on restart);
  it now stores `model_dump(mode="json")`. And async failures no longer return `str(e)`
  (`_public_backtest_error`, golden rule #12).

- **Backtest `start_date` bug fixed (TDD):** the engine silently simulated a truncated window
  while reporting the requested one (cache trusted as complete). `fetch_historical_data` now
  takes `start_date`; `backtest()` and `_get_benchmark_series` refetch when cache coverage is
  insufficient. New tests in `test_backtester.py`; `_fake_fetch` fixtures updated in
  `test_api_dirty_data.py`.
- **Per-IP defaults made env-configurable:** `RATE_LIMIT_PER_MINUTE=60` /
  `RATE_LIMIT_PER_DAY=1000` (weekly = 5× daily) drive the `read` bucket; documented in
  `.env.example`, staging files and `FRONTEND_API.md`; pinned in `test_rate_limits.py`.
- **Admin discovery:** `/auth/register` now applies the `ADMIN_EMAILS` bootstrap and returns
  real `is_admin` (was hardcoded False); shared `_bootstrap_admin_role` helper; frontend
  AppShell shows a conditional Admin nav link → `ADMIN_DASHBOARD_URL`.
- **Scheduler is a separate process** (`python -m src.main scheduler`), documented in
  `SCHEDULER.md` with the heartbeat/watchdog story (Fly restart covers the API; heartbeat
  alert covers the scheduler; manual `flyctl machines restart` recovery).
- **Staging + probes infrastructure:** `staging.env(.example)`, `seed_probes_db.py`,
  `run_probes.py` (probes vs the isolated `stratoscan_probes` DB), release-gate wiring in
  `deploy.yml`.
- **Alert delivery wired:** `send_alert` delivers via webhook (preferred) or SendGrid email,
  throttled per key, fire-and-forget; fired by `JobMonitor._alert_failed` and
  `scheduler._alert_if_stale`.
- **Docs gating + CORS:** `APP_ENV=production` kills docs; `CORS_ALLOW_ORIGINS` env-driven
  (locked down, no wildcard).
- **Async backtests durable:** `backtest_jobs` table + `interrupted` recovery on restart.
- **Universe hygiene:** stale rows excluded after 7 days; pink-sheet OTC rows pruned from the
  refreshed universe; market-cap sanity sweep on upsert.