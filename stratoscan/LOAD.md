# Load — where the free tiers actually break

> **Owner:** this file is the written load ceiling (Day 13): what was measured, on what, and which
> limit is hit first. Re-run `python load_test.py` after anything that changes request cost.

**Measured 2026-10-05** with `load_test.py` against `e2e_stack.py` (local Postgres, synthetic
12-stock universe, rate limiting OFF, 4 CPUs, 16 GB). Each level ran 15 s (`/screen`) or 30 s
(backtests). Every `/screen` request was unique, so the 60 s screen cache never answered for
the database. Backtests are measured **start → done** (start the job, poll until finished).

## The numbers

| endpoint | concurrent users | throughput | p50 | p95 | errors |
|---|---|---|---|---|---|
| `/screen` | 1 | 64.5/s | 15 ms | 21 ms | 0% |
| `/screen` | 8 | 72.1/s | 109 ms | 136 ms | 0% |
| `/screen` | 16 | 91.7/s | 164 ms | 232 ms | 0% |
| `/screen` | 32 | 99.6/s | 316 ms | 394 ms | 0% |
| `/screen` | 64 | 98.9/s | 641 ms | 717 ms | 0% |
| `/backtest/async` | 1 | 8.7/min | 7.8 s | 8.6 s | 0% |
| `/backtest/async` | 4 | 8.3/min | 27.3 s | 30.8 s | 0% |
| `/backtest/async` | 8 | 7.6/min | 62.0 s | 63.0 s | 0% |
| `/backtest/async` | 16 | 7.2/min | 124.4 s | 132.3 s | 0% |
| `/backtest/async` | 32 | 2.5/min | 169.5 s | 185.5 s | 75% |

Full table: `load_test.py --report`. Before the Day 13 pool fix the same run failed **10–20% of
backtests at 8 concurrent users and ~30–43% at 16** ("connection pool exhausted" → 503).

## What breaks, in order

1. **Backtest throughput: ~8–9 per minute, flat.** Each backtest is CPU-bound Python
   (simulation, walk-forward, parameter grid, 1,000-run Monte Carlo and bootstrap) running in the
   API process; more users only lengthen the queue. Every concurrent backtester adds ~8 s of
   wait: **8 at once → one-minute waits** (the budget used here), **16 → two minutes**. At **32**
   the shared DB pool (5 connections) cannot serve the pollers within its 15 s wait and polls
   get **503 + Retry-After** — clean, no 500s, no lost jobs, but the job outlives a 3-minute
   client patience. *This is the number that matters:* **about 8 backtests a minute per API
   process on this box; 8 people backtesting at the same moment is the comfortable ceiling.**
2. **Screening: ~100 unique screens a second** at 32–64 users with p95 under 0.75 s and no
   errors. Not a constraint for a long time.
3. **Production will be slower.** Fly runs one shared-CPU VM (`fly.toml`); the box above had 4
   CPUs (the backtest path mostly uses one, under the GIL). Treat the backtest figure as an upper
   bound and measure it on the real machine during the Day 15 rehearsal.

## Which limit binds first — the configured budgets

The rate limits are set below the engine's capacity on purpose; they are what a user hits:

| limit | value | vs. capacity |
|---|---|---|
| Global backtests (`backtest_llm`) | 200 / day | engine could run ~12,000/day (8.7/min) — the budget binds, not the CPU |
| Global screens (`screener_llm`) | 900 / day | DB could serve ~8.6 M/day |
| Per IP | 2 backtests/min, 10/day; 5 screens/min, 50/day | — |
| Free plan | 50 backtests / month / account | entitlement, not capacity |
| Email (SendGrid free ~100/day) | `auth_register` 50/day + digest ≤ 60/run | shared — the first *daily* external ceiling |
| Finnhub (10 keys × 60/min) | 600 calls/min | scheduler-only; users never spend it |

## When to start paying

- **Email first.** ~100 emails/day covers about 50 signups plus the weekly digest. More than
  ~40 new accounts a day, or more than ~60 digest recipients, needs a paid SendGrid tier (or the
  digest spread across days).
- **CPU next.** If the backtest queue regularly holds more than ~8 people, the API needs more CPU
  or the robustness work moved off the request process (a worker) — not more API machines (the
  scheduler must stay single, golden rule #1).
- **Database compute.** Neon suspends after ~5 minutes idle (`src/config.py`). Once real traffic
  arrives more often than that all day, the database stops suspending and compute climbs toward
  24 hours a day — watch Neon's compute-hours meter once traffic is continuous.
- The global LLM budgets (`screener_llm`, `backtest_llm`, `explain_llm`) are free-tier shields;
  raising them means paying for the LLM provider at the same time.
