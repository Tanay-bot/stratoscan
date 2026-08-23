# Stratoscan

> **Bridge from idea to candidate list.** A natural-language market terminal with 10 years of backtested history — free, forever.

Stratoscan turns plain-English investing ideas into ranked, backtested candidate lists. Type `"find undervalued small-cap tech stocks with rising momentum"` and get real US stocks filtered, scored, and backtested against SPY — no spreadsheets, no query language, no paywall.

---

## What it does

| Feature | How it works |
|---------|-------------|
| **Natural-language screener** | Type a query in plain English. An LLM (or the hybrid deterministic parser) translates it into structured filters, then queries a local PostgreSQL database of ~5,000 US stocks. Zero API calls per screen. |
| **Backtesting engine** | Simulates a strategy on 10 years of daily OHLCV with realistic fees, slippage, stop-loss, take-profit, and position sizing. Produces equity curves, Sharpe ratio, max drawdown, win rate, and a SPY benchmark comparison. |
| **Robustness analysis** | Every backtest includes Monte Carlo outcome spread, walk-forward in-sample/out-of-sample splits, confidence intervals on Sharpe, and a parameter-sensitivity grid — so you know if the edge is real. |
| **Portfolio simulation** | Combines N picks into one shared capital pool with equal or risk-based allocation and a rebalance schedule. Combined equity curve, drawdown, and Sharpe for the whole portfolio. |
| **Compare mode** | Pin one strategy as a baseline, run another, and see them side-by-side on the same symbol set. |
| **Async progress** | Backtests stream per-symbol progress so you see live status instead of a blank screen. |
| **CSV export** | Download simulated trades or screening results as CSV. |
| **News + sentiment** | Real-time news articles with LLM-scored bullish/bearish/neutral sentiment per stock. |
| **Admin dashboard** | Self-contained HTML dashboard for operators — users, jobs, health, config warnings. |
| **Hybrid NL parser** | Deterministic regex/dictionary for exact queries (sub-ms, zero cost), LLM fallback for fuzzy ones. |

**Everything is free.** There is no paywall, no premium tier, no credits. Registration exists only for per-user rate limits.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React + Vite Frontend                     │
│  Screener · Backtest · News · Dashboard · Compare · Export  │
│                    Deployed on Vercel                        │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST API (axios)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (Python 3.14)               │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │   Auth   │  │ Screener │  │Backtester│  │   News   │    │
│  │  (JWT)   │  │  (NL →   │  │ (10y sim │  │(sentiment│    │
│  │          │  │  filters)│  │ + Monte  │  │ + articles)   │
│  └────┬─────┘  └────┬─────┘  │  Carlo)  │  └────┬─────┘    │
│       │             │        └────┬─────┘       │           │
│       │             │             │              │           │
│  ┌────▼─────────────▼─────────────▼──────────────▼─────┐   │
│  │              PostgreSQL (3 databases)                 │   │
│  │  • Screener DB: stock_master + users                 │   │
│  │  • Backtest DB: 10y OHLCV history + results          │   │
│  │  • News DB: articles + LLM-scored sentiment          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │   LLM    │  │ Finnhub  │  │ yfinance │  │ Scheduler│    │
│  │  Router  │  │  Pool    │  │  (OHLCV) │  │ (refresh)│    │
│  │(failover)│  │(10 keys) │  │          │  │          │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                    Deployed on Fly.io                       │
└─────────────────────────────────────────────────────────────┘
```

### Four-layer pipeline

| Layer | What it does | Cost per query |
|-------|-------------|----------------|
| **1. NL Interpreter** | Translates plain English into structured filter criteria | 1 LLM call (or 0ms deterministic) |
| **2. Screener** | Filters ~5,000 US stocks from a local PostgreSQL DB | **0 API calls** |
| **3. Backtester** | Simulates a strategy on 10 years of daily bars | yfinance downloads (cached) |
| **4. Enrichment** | News + sentiment for top matches | Batch job, 0 calls per query |

### Key cost-control decisions

- **10 Finnhub keys** rotated round-robin → 600 req/min free tier, split across keys
- **Multi-provider LLM failover** (Z.ai, Gemini, Groq) — free-tier keys with instant failover
- **24h interpret cache** + **60s screen cache** — repeat queries burn zero LLM calls
- **Hybrid NL parser** — deterministic regex/dictionary for 80% of queries (sub-ms, zero cost), LLM fallback for the remaining 20%
- **Per-IP + global rate limits** via Upstash Redis (in-memory fallback)

### Tech stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.14, FastAPI, uvicorn, Pydantic v2 |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Zustand |
| Database | PostgreSQL (Neon) × 3 projects |
| Auth | Zero-dep JWT (HMAC-SHA256), PBKDF2 password hashing |
| Charts | Recharts (equity curves, drawdown, Monte Carlo) |
| Animations | Framer Motion |
| UI components | Radix UI primitives + custom design system |
| Error monitoring | Sentry (frontend) |
| CI/CD | GitHub Actions (4 workflows) |
| Deployment | Vercel (frontend) + Fly.io (backend, ~$3/mo) |
| Testing | Vitest (205 tests), pytest (hermetic suites) |

---

## Quick start

### Prerequisites

- **Python 3.14** (`pyenv install 3.14` or use the pinned `.python-version`)
- **Node.js 22+**
- A PostgreSQL database (Neon free tier works — just set `DATABASE_URL`)

### Backend setup

```bash
cd stratoscan

# Create virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your keys (see Environment Variables below)

# Initialize database
python -m src.main setup

# Run the server
python run.py
# → http://localhost:8000
# → Swagger docs: http://localhost:8000/docs
```

### Frontend setup

```bash
cd frontend

# Install dependencies
npm install

# Configure
cp .env.example .env
# For demo mode (no backend needed):
#   VITE_DEMO_MODE=true
# For real backend:
#   VITE_API_URL=http://localhost:8000

# Run the dev server
npm run dev
# → http://localhost:5173
```

### Docker (one command)

```bash
cd stratoscan
docker build -t stratoscan .
docker run --env-file .env -p 8000:8000 stratoscan
```

---

## Environment variables

All keys live in `.env` (gitignored). See `stratoscan/.env.example` for the full annotated list. The most important ones:

| Variable | Purpose | Required |
|----------|---------|----------|
| `DATABASE_URL` | PostgreSQL connection string (screener + users) | ✅ |
| `BACKTEST_DATABASE_URL` | Dedicated DB for 10y OHLCV + backtest results | ✅ prod |
| `NEWS_DATABASE_URL` | Separate DB for news articles + sentiment | for news |
| `JWT_SECRET` | Signs auth tokens. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | ✅ |
| `FINNHUB_KEY_01..10` | Stock data + news. Get free keys at finnhub.io (10 for round-robin) | ✅ |
| `SCREENER_LLM_KEY` | NL interpreter (Layer 1). Any OpenAI-compatible endpoint works | ✅ |
| `LLM_PROVIDERS` | Comma-separated priority list: `zai,gemini,groq` | for failover |
| `CORS_ALLOW_ORIGINS` | Comma-separated browser origins. Must include your frontend URL | ✅ prod |
| `RATE_LIMIT_ENABLED` | Master rate-limit switch. Keep `true` in production | ✅ |
| `ADMIN_EMAILS` | Comma-separated operator emails (auto-promoted to admin at login) | for admin |

### LLM provider setup

The interpreter routes through multiple free-tier providers with instant failover:

```bash
# Z.ai (free — GLM-4.5-Flash is $0 forever)
ZAI_BASE_URL="https://api.z.ai/api/paas/v4/"
ZAI_MODEL="glm-4.5-flash"
ZAI_KEYS="key1,key2,key3"

# Gemini (free tier ~1,500 RPD)
GEMINI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL="gemini-3-flash-preview"
GEMINI_KEYS="AIza..."

# Groq (free tier — 30 RPM / 1,000 RPD)
GROQ_BASE_URL="https://api.groq.com/openai/v1"
GROQ_MODEL="llama-3.3-70b-versatile"
GROQ_KEYS="gsk_..."

LLM_PROVIDERS="zai,gemini,groq"
```

If a provider rate-limits or goes down, the router fails over instantly to the next one. No code changes needed.

---

## API reference

Interactive docs at `http://localhost:8000/docs` (Swagger) and `/redoc` (ReDoc).

### Core endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/screen` | — | NL query → matching stocks (paginated) |
| `POST` | `/backtest` | JWT | Backtest a strategy on 10 years of history |
| `POST` | `/backtest/async` | JWT | Start async backtest (poll for progress) |
| `GET` | `/backtest/async/{job_id}` | JWT | Poll async backtest status |
| `POST` | `/backtest/export` | JWT | Download simulated trades as CSV |
| `POST` | `/screen/export` | — | Download screening results as CSV |
| `POST` | `/enrich` | — | Screen + attach news/sentiment |
| `POST` | `/interpret` | — | Debug: NL query → extracted filters only |

### Auth endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/register` | Create account (free). Returns JWT + `email_sent` flag |
| `POST` | `/auth/login` | Get JWT. Blocked for unverified accounts when `EMAIL_VERIFICATION_REQUIRED=true` |
| `GET` | `/auth/me` | Current user profile |
| `GET/POST` | `/auth/verify-email` | Verify via emailed token |
| `POST` | `/auth/resend-verification` | Re-send verification email |

### Data endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/stocks` | Browse the stock universe (paginated) |
| `GET` | `/stocks/{symbol}` | Single stock details + sentiment |
| `GET` | `/news` | News feed with sentiment |
| `GET` | `/news/{symbol}` | Per-stock news + sentiment |
| `GET` | `/health` | Server + DB health (real `SELECT 1` probe) |
| `GET` | `/meta` | Capability manifest (endpoints, filter fields, rate limits) |

### Admin endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin` | admin | Self-contained HTML dashboard |
| `GET` | `/admin/overview` | admin | Aggregated system state |
| `GET` | `/admin/users` | admin | Paginated user list |
| `PATCH` | `/admin/users/{id}` | admin | Update plan / admin role / verified |
| `GET` | `/admin/jobs` | admin | Background job monitor |

### Error shape

Every non-2xx response uses the same envelope:

```json
{
  "error": true,
  "detail": "human-readable message",
  "status_code": 429,
  "retry_after": 53233
}
```

### Rate limits

| Scope | Burst | Daily | Weekly |
|-------|-------|-------|--------|
| `screen` | 5/min | 50 | 250 |
| `backtest` | 2/min | 10 | 50 |
| `enrich` | 3/min | 20 | 100 |
| `read` | 60/min | 1,000 | 5,000 |

Per-IP limits are configurable via `RATE_LIMIT_PER_MINUTE` and `RATE_LIMIT_PER_DAY`. Global LLM buckets cap total usage across all users (`screener_llm` 900/day, `backtest_llm` 200/day).

---

## Frontend pages

| Page | Route | What it does |
|------|-------|-------------|
| **Landing** | `/` | Hero with live ticker tape, features, pricing, how-it-works |
| **Screener** | `/app/screener` | NL query → ranked stock results with filters, sort, pagination |
| **Backtest** | `/app/backtest` | 3-pane workspace: strategy chat, results (KPIs + equity curve + robustness), generated code |
| **News** | `/app/news` | Browse latest news with sentiment badges |
| **Dashboard** | `/app/dashboard` | Personal watchlist, saved screeners, recent backtests |
| **Settings** | `/app/settings` | Account settings, email verification status |
| **Admin** | `/admin` | Backend-served operator dashboard (users, jobs, health) |

### Backtest features

- **Strategy chat**: type natural language, get a full backtest
- **Tune panel**: RSI thresholds, SMA periods, stop-loss, take-profit, position sizing, portfolio allocation
- **KPI cards**: total return, Sharpe ratio, max drawdown, win rate (vs SPY benchmark)
- **Equity curve**: SVG chart with strategy vs SPY lines and 5–95% confidence spread
- **Robustness blocks**: Monte Carlo, confidence intervals, walk-forward IS/OOS, parameter sensitivity grid
- **Compare mode**: pin one strategy as baseline, run another on the same symbol set
- **Per-symbol results**: tabbed view with individual equity curves, trades, data-age badges
- **Async progress**: live per-symbol status while the backtest runs
- **CSV export**: download simulated trades or screening results
- **Shareable links**: serialize all parameters into a URL for sharing

---

## Project structure

```
stratoscan/                     # Backend (Python)
├── src/
│   ├── api.py                  # FastAPI REST layer (all endpoints)
│   ├── interpreter.py          # NL → structured filters (LLM + deterministic hybrid)
│   ├── deterministic_interpreter.py  # Hybrid NL parser (regex/dictionary + LLM fallback)
│   ├── screener.py             # DB-backed filtering + composite scoring
│   ├── backtester.py           # 10y simulation engine (Monte Carlo, walk-forward, portfolio)
│   ├── enrichment.py           # News + sentiment enrichment
│   ├── news.py                 # News pipeline (stored sentiment)
│   ├── llm_router.py           # Multi-provider failover (free keys)
│   ├── data_fetcher.py         # Finnhub pool + round-robin refresh
│   ├── database.py             # PostgreSQL pool (screener/news/users)
│   ├── auth.py                 # Zero-dep password hash + JWT
│   ├── cache.py                # Thread-safe TTL cache (stdlib)
│   ├── config.py               # Env-driven settings + validation
│   ├── scheduler.py            # Background refresh jobs
│   ├── cache_warmer.py         # Backtest history pre-fetcher
│   ├── job_monitor.py          # Background job monitoring (job_runs table)
│   ├── redis_ratelimit.py      # Redis-backed rate limiting (Upstash)
│   ├── admin_dashboard.py      # Self-contained HTML admin dashboard
│   ├── emails.py               # SendGrid verification emails (stdlib-only)
│   ├── alerts.py               # Webhook + email alerting
│   ├── metrics.py              # Prometheus-format metrics
│   └── logging_config.py       # Structured logging setup
├── tests/                      # Hermetic test suites (faked DB + LLM)
├── alembic/                    # Database migrations
├── accuracy_loop.py            # 30-query accuracy verification loop
├── rule_execution_loop.py      # Rule-execution ground truth tests
├── Dockerfile                  # Production container (Python 3.14-slim)
├── fly.toml                    # Fly.io production config
├── fly.staging.toml            # Fly.io staging config
├── requirements.txt            # Human-readable dependency ranges
├── requirements-lock.txt       # Pinned + SHA256-hashed (production)
└── pyproject.toml              # Modern Python packaging

frontend/                       # Frontend (React + Vite)
├── src/
│   ├── App.tsx                 # Router + route definitions
│   ├── main.tsx                # Entry point (Sentry init, analytics)
│   ├── index.css               # Global styles + Tailwind config
│   ├── pages/
│   │   ├── Landing.tsx         # Marketing landing page
│   │   ├── Screener.tsx        # NL screener with filters/sort/pagination
│   │   ├── Backtest.tsx        # 3-pane backtest workspace (1,115 lines)
│   │   ├── News.tsx            # News feed with sentiment
│   │   ├── Dashboard.tsx       # Personal watchlist/saved screeners/recent backtests
│   │   ├── Settings.tsx        # Account settings
│   │   ├── Auth.tsx            # Login/register with demo mode
│   │   ├── VerifyEmail.tsx     # Email verification page
│   │   └── ...                 # Legal pages, 404, features, pricing, about
│   ├── components/
│   │   ├── backtest/           # ComparePanel, SplitPane, ChatPanel, CodePanel, Robustness
│   │   ├── charts/             # EquityCurve (Recharts SVG)
│   │   ├── ui/                 # Radix-based design system (Button, Badge, Input, etc.)
│   │   ├── landing/            # Landing page sections
│   │   └── layout/             # Navigation, footer
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts       # Axios instance + auth interceptor + error normalization
│   │   │   ├── services.ts     # Typed API functions (screen, backtest, export, etc.)
│   │   │   ├── types.ts        # TypeScript mirror of the backend contract
│   │   │   ├── schemas.ts      # Zod runtime validation schemas
│   │   │   └── demo.ts         # Demo mode mock data (1,390 lines)
│   │   ├── backtestParams.ts   # Shareable link serialize/parse
│   │   ├── utils.ts            # Formatters (fmtPct, fmtCurrency, cn)
│   │   └── series.ts           # Chart data processing
│   ├── store/
│   │   ├── auth.ts             # Auth state (Zustand)
│   │   ├── personal.ts         # Watchlist + saved screeners + recent backtests
│   │   ├── rateLimit.ts        # 429 countdown + auto-retry
│   │   └── notifications.ts    # Toast notifications
│   └── hooks/                  # Custom hooks (useKeyboardShortcuts, useReducedMotion)
├── vitest.config.ts            # Test config (jsdom + React)
├── vite.config.ts              # Build config (proxy, Sentry, Tailwind)
├── package.json                # Dependencies + scripts
└── tailwind.config.js          # Design tokens

.github/workflows/
├── ci.yml                      # Backend accuracy gate (ruff + golden set + hermetic suites)
├── frontend-ci.yml             # Frontend gate (typecheck + lint + format + test + build)
├── deploy.yml                  # Backend → Fly.io (gate + secrets + deploy + healthcheck + rollback)
└── frontend-deploy.yml         # Frontend → Vercel (gate + deploy + healthcheck)
```

---

## Development

### Running locally

```bash
# Terminal 1: Backend
cd stratoscan
source .venv/bin/activate
python run.py                    # → http://localhost:8000

# Terminal 2: Frontend
cd frontend
npm run dev                      # → http://localhost:5173
# Vite proxies /api/* → localhost:8000 (zero CORS)
```

### Demo mode (no backend needed)

Set `VITE_DEMO_MODE=true` in `frontend/.env` to run the full UI with realistic mock data. All API functions serve synthetic data through the same typed interface — perfect for presentations or offline development.

### Useful commands

```bash
# Backend
python -m pytest                        # Run all hermetic tests
python accuracy_loop.py                 # Run 30-query accuracy verification
python -m src.main scheduler            # Start the background scheduler
python -m src.main warm-cache           # One-time backtest history cache warm

# Frontend
npm test                                # Run vitest (205 tests)
npm run typecheck                       # TypeScript strict mode check
npm run lint                            # ESLint
npm run format:check                    # Prettier check
npm run build                           # Production build
npm run verify:behavior                 # E2E behavioral verification
```

### Testing

**Backend** (hermetic — no network, no quota burn):

```bash
cd stratoscan
python -m tests.test_imports            # Module import + API smoke
python -m tests.test_backtest_golden    # Frozen golden values (any math change fails)
python verify_backtest_accuracy.py      # Hand-computed ground truth
python verify_trade_pnl.py              # Cash P&L verification
pytest                                  # Full suite
```

**Frontend** (vitest + jsdom):

```bash
cd frontend
npm test                                # 205 tests across 26 files
npm run verify:walkforward              # Walk-forward scale guard
npm run verify:behavior                 # E2E behavioral verification
```

---

## Deployment

### Architecture

| Service | Platform | Cost | Auto-deploy |
|---------|----------|------|-------------|
| Frontend | Vercel | Free tier | On push to `main` |
| Backend | Fly.io | ~$3/mo (always-on) | On push to `main` |
| Database | Neon | Free tier | — |
| Redis | Upstash | Free tier | — |

### CI/CD pipelines

Four GitHub Actions workflows run on every push to `main`:

1. **`ci.yml`** — Backend accuracy gate: ruff lint, frozen golden set, hand-computed backtest accuracy, 6 hermetic test suites
2. **`frontend-ci.yml`** — Frontend gate: TypeScript, ESLint, Prettier, vitest, walk-forward scale guard, production build
3. **`deploy.yml`** — Backend → Fly.io: gate → stage secrets → deploy → healthcheck → rollback on failure
4. **`frontend-deploy.yml`** — Frontend → Vercel: gate → deploy → healthcheck

### First deploy (manual)

See `DEPLOY.md` for the complete step-by-step runbook. Summary:

```bash
# Backend (Fly.io)
cd stratoscan
fly launch --no-deploy --name stratoscan-api
fly secrets set DATABASE_URL=... JWT_SECRET=... FINNHUB_KEY_01=...  # (see DEPLOY.md for full list)
fly deploy

# Frontend (Vercel)
cd frontend
npx wrangler pages deploy dist --project-name stratoscan
# Or connect GitHub in the Vercel dashboard for auto-deploy
```

### Required GitHub Actions secrets

| Secret | For | Where to get it |
|--------|-----|-----------------|
| `FLY_API_TOKEN` | Backend deploy | Fly.io → Account → Access Tokens |
| `VERCEL_TOKEN` | Frontend deploy | Vercel → Account → Tokens |
| `VERCEL_ORG_ID` | Frontend deploy | Vercel → Account → General |
| `VERCEL_PROJECT_ID` | Frontend deploy | Vercel → Project → General |

Backend secrets (`DATABASE_URL`, `JWT_SECRET`, etc.) are stored as Fly.io secrets via `fly secrets set`, not in GitHub Actions.

---

## Background jobs

The scheduler runs as a separate process (`python -m src.main scheduler`) and manages:

| Job | Schedule | What it does |
|-----|----------|-------------|
| `price_refresh` | Every 15 min | Fetches latest quotes via Finnhub (10-key round-robin) |
| `technical_refresh` | Daily | Computes RSI, SMA, MACD, Bollinger bands, etc. |
| `fundamental_refresh` | Weekly | Updates P/E, margins, growth, EV/EBITDA |
| `news_refresh` | Every 2 hours | Fetches + LLM-scores news for top-150 by market cap |
| `backtest_cache_warmer` | Daily at 03:30 | Pre-fetches 10y OHLCV for top-100 stocks |

Every scheduler run is recorded to the `job_runs` table and exposed via `GET /ops/jobs`. The scheduler emits a throttled heartbeat so a silently-dead refresh is never missed.

**Important:** Run exactly ONE scheduler process. Do not scale to multiple machines or the jobs will duplicate.

---

## Rate limiting

Every request is rate-limited per IP across burst (1 min), daily (24h), and weekly (7d) windows. The 429 response includes `retry_after` (seconds) and a `Retry-After` header.

The frontend handles this automatically:
- Submit buttons show a countdown: `"Please wait 30s"`
- When the countdown ends, the request auto-retries
- The rate-limit state is shared across all submit surfaces via Zustand

### Budget protection

| Scope | Burst | Daily | Purpose |
|-------|-------|-------|---------|
| `screen` | 5/min | 50 | LLM + DB cost shield |
| `backtest` | 2/min | 10 | LLM + yfinance cost shield |
| `enrich` | 3/min | 20 | Finnhub + LLM cost shield |
| `read` | 60/min | 1,000 | Generic (configurable) |

---

## Accuracy & quality

### 30-query accuracy loop

`stratoscan/accuracy_loop.py` throws 30 diverse queries at the backend and independently recomputes every reported number from the response:

- Per-trade PnL vs actual cash delta with fees (long & short)
- Cash conservation: `final_value == initial + sum(PnL)`
- Win rate, total trades, equity curve endpoints
- Benchmark/excess return consistency
- Strategy echo (stops, sizing, buy-and-hold rules)
- Garbage input fails honestly (no fake success)

**Current result: 14,294/14,294 checks passed (100.00%)**

### Golden set

`tests/test_backtest_golden.py` contains frozen expected values for specific strategies. Any change to the backtester math — fees, slippage, signal generation, portfolio simulation — changes a number and fails loudly.

### CI regression gates

- Backend: ruff lint → golden set → ground truth → 6 hermetic suites
- Frontend: TypeScript → ESLint → Prettier → vitest → walk-forward scale guard → production build

---

## Roadmap

- [x] NL interpreter (multi-provider LLM failover + hybrid deterministic parser)
- [x] Real-time screener (0 API calls per query)
- [x] Backtesting engine (10y simulation, Monte Carlo, walk-forward, portfolio)
- [x] Auth (JWT, email verification, admin roles)
- [x] News + sentiment pipeline
- [x] Rate limiting, caching, pagination
- [x] Frontend (screener UI, backtest workspace, compare mode, async progress)
- [x] CSV export (trades + screening results)
- [x] Shareable backtest links
- [x] CI/CD (4 GitHub Actions workflows)
- [x] Production deploy (Vercel + Fly.io)
- [ ] Real-time price data (WebSocket streaming)
- [ ] Brokerage integration (paper trading via Alpaca)
- [ ] Multi-device personal data sync improvements
- [ ] Advanced strategy builder (visual drag-and-drop)

---

## License

Proprietary — see repository access controls.

---

**Built with 75,000+ lines of code across Python and TypeScript. Free forever. No paywall.**
