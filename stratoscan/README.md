# Stratoscan 🚀

**AI-powered stock screener and backtesting platform.**

> Not a stock-picking service. A bridge from idea to candidate list.

Turn a plain-English investment idea into a ranked list of real US stocks —
then backtest the strategy on 10 years of history before you commit a
single dollar. **Everything is free — no paywall.**

---

## Architecture

Four layers, each designed around **near-zero operating cost**:

| Layer | What it does | Cost |
|---|---|---|
| **1. NL Interpreter** | Translates "find undervalued small-cap tech stocks with rising momentum" into structured filters | 1 LLM call per query (free-tier keys) |
| **2. Real-Time Screener** | Filters ~5,000 US stocks from a local PostgreSQL DB | **0 API calls per query** |
| **3. Backtesting Engine** | Simulates a strategy on 10 years of OHLCV with fees + slippage, stops, sizing, Monte Carlo, walk-forward | yfinance downloads (cached in the backtest DB — Neon in prod, SQLite in dev) |
| **4. Enrichment** | News + sentiment for top matches, stored in a separate news DB | Batch job, 0 calls per query |

Key cost-control decisions:

- **10 Finnhub keys** rotated round-robin → 600 req/min free tier, split
  across keys. A background scheduler refreshes the DB; user queries never
  hit Finnhub.
- **Free-tier LLM keys** routed through `src/llm_router.py` with instant
  failover — one provider cooling down never blocks a user. `LLM_PROVIDERS`
  sets the priority order; the shipped default is Z.ai (`glm-4.5-flash`),
  then Gemini, then Groq. ZenMux (`z-ai/glm-4.7-flash-free`) is the legacy
  `LLM_BASE_URL`/`LLM_MODEL` fallback used when no provider has keys.
  `.env.example` owns the exact endpoints and model ids.
- **Interpret cache (24h)** + **screen cache (60s)** so repeat queries burn
  zero LLM calls.
- **Weekly + daily rate limits** per IP and shared global buckets protect
  the free tier from abuse without punishing real humans.

---

## Quickstart

### 1. Prerequisites

- **Python 3.14** (see `.python-version`; `pyenv install 3.14` or use the
  pinned version in the Dockerfile)
- A PostgreSQL database (Supabase / Neon / Railway — any provider works;
  just set `DATABASE_URL`)

### 2. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-lock.txt   # exact pins + SHA256 hashes
# or the loose ranges:
pip install -r requirements.txt
# or as a package:
pip install -e .
```

### 3. Configure

```bash
cp .env.example .env
# Fill in at minimum:
#   DATABASE_URL, JWT_SECRET, FINNHUB_KEY_01..10, SCREENER_LLM_KEY
python -m src.main setup   # runs Alembic migrations + creates tables
```

### 4. Run

```bash
python run.py                    # dev server on :8000 with hot reload
# or
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

Docs land at **http://localhost:8000/docs** (Swagger) and `/redoc`.

### 5. Docker (one command)

```bash
docker build -t stratoscan .
docker run --env-file .env -p 8000:8000 stratoscan
```

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Server + DB health (real `SELECT 1` probe) |
| GET | `/config` | — | Which services are configured |
| POST | `/screen` | — | **Free**: NL query → matching stocks (paginated) |
| POST | `/interpret` | — | Debug: NL query → extracted filters only |
| POST | `/enrich` | — | Screen + attach news/sentiment |
| POST | `/backtest` | JWT | Backtest a strategy (free for all authenticated users) |
| POST | `/auth/register` | — | Create account (free — auth only for per-user rate limits). New accounts get a verification email (`email_sent` in the response) |
| POST | `/auth/login` | — | Get JWT (blocked for unverified accounts when `EMAIL_VERIFICATION_REQUIRED=true`) |
| GET | `/auth/me` | JWT | Current profile |
| GET/POST | `/auth/verify-email` | — | Verify via the emailed token (link click / JSON body) |
| POST | `/auth/resend-verification` | JWT | Send a fresh verification link |
| GET | `/stocks`, `/stocks/{symbol}` | — | Browse the universe |
| GET | `/news`, `/news/{symbol}` | — | News feed + sentiment |
| POST | `/refresh` | JWT (admin) | Trigger async Finnhub refresh (thousands of Finnhub calls — admin-gated) |
| GET | `/admin` | JWT (admin) | Self-contained operator dashboard (HTML) — users, jobs, health |
| GET | `/admin/overview` | JWT (admin) | User counts, plans, verification stats |
| GET | `/admin/users` | JWT (admin) | Paginated user list |
| PATCH | `/admin/users/{id}` | JWT (admin) | Grant/revoke admin, change plan |
| GET | `/admin/jobs` | JWT (admin) | All monitored background jobs |

### Admin dashboard

Operators reach a self-contained HTML dashboard at `GET /admin` (no
frontend build needed). Access is role-gated: an account must have
`is_admin=true`. Grant it either statically via the comma-separated
`ADMIN_EMAILS` env var (auto-promoted at login) or at runtime via
`PATCH /admin/users/{id}`. Without `ADMIN_EMAILS` set, every admin
endpoint returns 403.

### Pricing: free, full stop

There is no paywall. `/screen`, `/enrich`, and `/backtest` are free for
everyone. Registration (`/auth/register`) exists only to give each user
per-user rate limits — the backtesting engine requires an authenticated
JWT, but every account has access. The old Stripe billing layer
(`/billing/*`, `src/billing.py`) was removed.

---

## Environment Variables

All keys live in `.env` (gitignored, auto-chmod 600). See `.env.example`
for the full annotated list. Highlights:

| Variable | Purpose |
|---|---|
| `FINNHUB_KEY_01..10` | Round-robin Finnhub pool (600 req/min) |
| `NEWS_FINNHUB_KEY` | Dedicated key for news (never competes with screener) |
| `SCREENER_LLM_KEY` / `BACKTEST_LLM_KEY` | Free-tier LLM keys |
| `LLM_PROVIDERS` + `<NAME>_BASE_URL/_MODEL/_KEYS` | Multi-provider failover pool (zai, zenmux, gemini, groq…) |
| `DATABASE_URL` | Primary PostgreSQL (screener + users) |
| `NEWS_DATABASE_URL` | Separate PostgreSQL for news (keeps bloat off screener DB) |
| `BACKTEST_DATABASE_URL` | **Dedicated** PostgreSQL for 10y OHLCV + backtest results (unset → local SQLite dev fallback) |
| `JWT_SECRET` | Signs auth tokens (HMAC-SHA256, zero deps) |
| `SENDGRID_API_KEY` / `EMAIL_FROM_ADDRESS` | Signup email verification (stdlib-only SendGrid Web API client, `src/emails.py`; sender must be verified in SendGrid) |
| `APP_BASE_URL` | Public origin used to build the emailed verification link (dev: `http://localhost:3000`) |
| `EMAIL_VERIFICATION_REQUIRED` | When `true`, unverified accounts are blocked from login (403 + resend hint). Default `false` keeps existing accounts working |
| `CORS_ALLOW_ORIGINS` | **Locked down**: browser origins, comma-separated |
| `RATE_LIMIT_ENABLED` | Master switch for the unified rate-limit middleware |
| `REDIS_RATE_LIMIT_ENABLED` + `UPSTASH_REDIS_REST_URL/_TOKEN` | **Redis-backed** limits: every worker shares ONE counter (in-memory fallback if Redis is down) |
| `CACHE_ENABLED` + TTLs | Interpret (24h) + screen (60s) caches |
| `JOB_MONITOR_ENABLED` / `JOB_HEARTBEAT_PERSIST_MINUTES` | Background-job monitoring: every scheduler run is recorded (`job_runs` table) + a throttled scheduler heartbeat. Exposed via `GET /ops/jobs` |
| `BACKTEST_CACHE_WARMER_ENABLED` / `_LIMIT` / `_STALE_DAYS` / `_TIME` | Backtest cache warmer: pre-fetches 10y yfinance OHLCV for the screener's top-N stocks (default 100, nightly 03:30) so backtests never wait on a cold download |
| `ADMIN_EMAILS` | Comma-separated operator emails auto-promoted to admin at login (gates `/admin` + `/admin/*`; unset → 403 everywhere) |

---

## Background Jobs

```bash
python -m src.main scheduler        # price refresh every 15 min, fundamentals daily
python -m src.main warm-cache       # one-time backtest history cache warm
python -m src.main refresh-news     # one-time news + sentiment refresh
python -m src.main refresh-news     # news + sentiment for top-N by market cap
python -m src.main refresh          # one-shot price refresh
```

Every scheduler run is recorded to the `job_runs` table (status, duration,
record count, error) and the scheduler emits a throttled heartbeat
(`scheduler_heartbeat`) so a silently-dead refresh is never missed.

> ℹ️ **The scheduler runs as its own process** (`python -m src.main scheduler`),
> separate from the API server. For the full watchdog story — heartbeat
> liveness rules, the `Scheduler health alert` / `Background job failed`
> webhook paths, Fly restart-on-crash coverage, and the manual restart
> procedure (`flyctl machines restart <machine-id>`) — see
> [`SCHEDULER.md`](./SCHEDULER.md).

| Endpoint | What it tells you |
|---|---|
| `GET /ops/jobs` | Live status of every job: latest run, consecutive failures, **staleness flag** (last success older than interval × 3) + scheduler liveness from the heartbeat |
| `GET /ops/jobs/{job_name}` | Recent run history for one job (newest first) |

Known job names: `price_refresh`, `technical_refresh`, `fundamental_refresh`,
`news_refresh`, `backtest_cache_warm`, `manual_refresh`, `historical_backfill`
(the authoritative list is `JOB_DEFINITIONS` in `src/job_monitor.py`). The heartbeat is persisted at most every
`JOB_HEARTBEAT_PERSIST_MINUTES` (default 15) so Neon can still auto-suspend
between refresh cycles; "scheduler alive" is judged at 3× that interval.
In-memory fallback when no database — monitoring never breaks a refresh job.

---

## Testing

The suites are hermetic (faked DB + LLM — no network, no quota burn). The
runtime lock file (`requirements-lock.txt`) doesn't include dev tools, so
install them once before running tests:

```bash
pip install -e ".[dev]"     # or: pip install -r requirements.txt
python -m tests.test_imports        # all modules import + API smoke
python -m tests.test_auth           # passwords, JWTs, register/login, /backtest auth gate
python -m tests.test_pagination     # /screen offset paging
python -m tests.test_cache          # TTL cache + interpret/screen caching
python -m tests.test_llm_router     # provider failover logic
python -m tests.test_rate_limits    # approved limit numbers locked
python -m tests.test_api_dirty_data # dirty-data /backtest at the HTTP surface
# or with pytest:
pytest
```

### Release gate + migration dry-run

Before any push to production, two release-blocking steps run against the
staging environment:

- **Behavior probes (R2):** [`RELEASE_GATE.md`](./RELEASE_GATE.md) + [`STAGING.md`](./STAGING.md) —
  `scripts/run_release_gate.sh` runs all 18 `verify_*.py` probes against the
  isolated `stratoscan_probes` DB and blocks the deploy if any fails.
- **Migration dry-run (P1-6):** `scripts/migration_dryrun.py` replays
  `alembic upgrade head` against a throwaway **copy of the production
  database** (schema + data cloned via binary COPY, prod opened read-only,
  copy dropped afterwards) and verifies the head is stamped with **zero
  data change** — a built-in tripwire proves the run never migrated prod.
- **PITR restore drill (P1-4):** `scripts/pitr_restore_drill.py` proves a
  **restored copy of the primary database** serves real screens and
  backtests — either your real Neon Console restore (`--scratch-url`) or a
  restore-equivalent timed clone, then a **hand-verified AAPL equity curve**
  (every point recomputed from the documented fill math) and a read-only
  guarantee. See `STAGING.md` → "PITR restore drill".
- **Failure drills (P1-7):** [`FAILURE_DRILLS.md`](./FAILURE_DRILLS.md) +
  `verify_failure_drills.py` — four hermetic drills proving the system
  degrades gracefully when upstreams die: all 10 Finnhub keys dead
  (retries + fail-fast + DB never touched), all LLM providers down
  (clean "could not interpret", never a 500), yfinance timeout (clean
  zero-data result), Upstash down (fail-open to the in-memory rate
  limiter, still limiting). Probe #7 in the release gate.

---

## Project Layout

```
stratoscan/
├── src/
│   ├── api.py          # FastAPI REST layer
│   ├── interpreter.py  # Layer 1 — NL → structured filters (LLM)
│   ├── screener.py     # Layer 2 — DB-backed filtering + momentum
│   ├── backtester.py   # Layer 3 — historical simulation (free)
│   ├── enrichment.py   # Layer 4 — news/sentiment (live fetch)
│   ├── news.py         # News pipeline (stored sentiment)
│   ├── llm_router.py   # Multi-provider failover (free keys)
│   ├── data_fetcher.py # Finnhub pool + round-robin refresh
│   ├── database.py     # PostgreSQL (screener/news/users) + SQLite (backtest)
│   ├── auth.py         # Zero-dep password hash + JWT
│   ├── cache.py        # Thread-safe TTL cache (stdlib)
│   ├── scheduler.py    # Background refresh jobs
│   └── config.py       # Env-driven settings + validation
├── alembic/            # DB migrations (raw SQL)
├── tests/              # Hermetic test suites + fakes
├── requirements.in     # Exact tested versions (pip-tools input)
├── requirements-lock.txt  # Pinned + SHA256-hashed (production install)
├── pyproject.toml      # Modern packaging: pip install -e .
├── .python-version     # 3.14
└── Dockerfile          # One-command deploy
```

---

## Roadmap

- [x] NL interpreter (free LLM keys, cost discipline)
- [x] Real-time screener (0 API calls per query)
- [x] Backtesting engine (free, rule-based strategies)
- [x] Auth (JWT) — no paywall; everything is free
- [x] Signup email verification (SendGrid, stdlib-only client, alembic migration)
- [x] News + sentiment pipeline (dedicated key + separate DB)
- [x] Rate limits, caching, pagination, configurable timeouts
- [x] Frontend (screener UI + backtest workspace) — `frontend/`, deployed on Vercel
- [x] Redis-backed rate limiting for multi-worker exactness (Upstash REST, src/redis_ratelimit.py)
