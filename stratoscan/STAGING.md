# Stratoscan — Staging Environment (P0-0 + P0-12)

> **Owner:** this file is the source of truth for the staging and probes databases — creating, seeding and isolating them.
> The probe list → `RELEASE_GATE.md`. Secrets → `SECRETS.md`.

A fully isolated staging environment where E2E tests, `verify_*.py` probes,
and the release-gate rounds (R2–R6) run **without touching production data
or tripping production rate limits**. Created because the release checklist
references "staging" 6+ times but no staging environment existed.

## What it is

| Resource | Value | Notes |
|---|---|---|
| **Staging database** | `stratoscan_staging` — a second database in the **same Neon project** as production | Created via `CREATE DATABASE stratoscan_staging` (schema copy approach allowed by the task). Same host/user/password; only the database path differs. |
| **Probes database** | `stratoscan_probes` — a THIRD database in the same Neon project, dedicated to the `verify_*.py` behavior probes (P0-12) | Probes mutate/abuse rows as part of their job — they must never touch prod **or** the E2E staging DB. Isolation is structural: a separate database. |
| **Staging env file** | `staging.env` (gitignored) + `staging.env.example` (committed template) | `DATABASE_URL` → `stratoscan_staging`; `STAGING_DATABASE_URL` → `stratoscan_probes`; `RATE_LIMIT_ENABLED=false`; `REDIS_RATE_LIMIT_ENABLED=false` |
| **Staging Fly app** | `fly.staging.toml` → app `stratoscan-staging` | Separate machine from prod, always-on, `/health` check |
| **Staging server (local)** | `run_staging.py` → port **8010** | Loads `staging.env` only; production `.env` untouched |
| **Seeding (staging)** | `seed_staging.py` | Copies top-50 (default) / `--all` real rows from prod stock_master + creates full schema |
| **Seeding (probes)** | `seed_probes_db.py` | **100+ stocks with real data**: top-100 by market cap from prod (AAPL guaranteed) + 20 random symbols + **real SPY OHLCV from yfinance** (ETFs aren't in the equity universe). Creates the full schema on `stratoscan_probes`. |
| **Probe runner** | `run_probes.py` | Runs every probe in its `PROBES` list against `stratoscan_probes` with a pass/fail report. Boots a dedicated rate-limited server for `verify_throughput` (via `probe_server_entry.py`). This is the R2 release-gate entry point. |

**Safety invariants**
- `run_staging.py` refuses to start unless `DATABASE_URL` contains `stratoscan_staging`.
- `seed_staging.py` refuses to run unless the target URL contains `stratoscan_staging`.
- `seed_probes_db.py` refuses to run unless the target database path is exactly `stratoscan_probes`.
- `run_probes.py` refuses to run unless `STAGING_DATABASE_URL` contains `stratoscan_probes`, and it forces `DATABASE_URL` to the probes DB for every subprocess — no probe can touch prod or the E2E staging DB.
- `staging.env` is gitignored; only `staging.env.example` (masked) is committed.
- Staging shares the news + backtest databases with prod (read-mostly). Staging
  writes go only to the staging stock_master. If full news/backtest isolation is
  ever needed, provision two more Neon projects and point `NEWS_DATABASE_URL` /
  `BACKTEST_DATABASE_URL` at them.

## First-time setup (done by P0-0, documented for reproducibility)

```bash
cd stratoscan

# 1. Create the staging database in the SAME Neon project (schema copy).
#    Requires a connection with CREATEDB — the production role has it.
/usr/local/bin/python3.14 - <<'PY'
import sys; sys.path.insert(0,'.')
from dotenv import load_dotenv; load_dotenv(".env")
import os, psycopg2
from urllib.parse import urlsplit, urlunsplit
prod = os.environ["DATABASE_URL"]
direct = prod.replace("-pooler.c-", ".c-")   # CREATE DATABASE needs the direct endpoint
conn = psycopg2.connect(direct, connect_timeout=15); conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname='stratoscan_staging'")
if not cur.fetchone():
    cur.execute("CREATE DATABASE stratoscan_staging")
conn.close()
print("stratoscan_staging ready")
PY

# 2. Create staging.env from the example and fill in real values.
#    DATABASE_URL: swap ONLY the db path neondb -> stratoscan_staging.
cp staging.env.example staging.env   # then edit values (or copy from .env)

# 3. Seed with the top-50 stocks (real data, zero Finnhub calls).
/usr/local/bin/python3.14 seed_staging.py            # top 50 by market cap
/usr/local/bin/python3.14 seed_staging.py --all      # whole exchange-listed universe

# 4. Run the staging API locally (port 8010).
/usr/local/bin/python3.14 run_staging.py --no-reload
```

## Probes database (P0-12) — setup, refresh, run

```bash
cd stratoscan

# 1. Create the probes database (same Neon project, direct endpoint).
/usr/local/bin/python3.14 - <<'PY'
import sys; sys.path.insert(0,'.')
from dotenv import load_dotenv; load_dotenv("staging.env")
import os, psycopg2
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
url = os.environ["DATABASE_URL"]
p = urlsplit(url)
direct_host = (p.hostname or "").replace("-pooler", "")
q = dict(parse_qsl(p.query))
direct = urlunsplit((p.scheme, p.netloc.replace(p.hostname or "", direct_host), "/neondb", urlencode(q), p.fragment))
conn = psycopg2.connect(direct, connect_timeout=15); conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname='stratoscan_probes'")
if not cur.fetchone():
    cur.execute("CREATE DATABASE stratoscan_probes")
conn.close()
print("stratoscan_probes ready")
PY

# 2. Make sure staging.env has STAGING_DATABASE_URL (same host/user as
#    DATABASE_URL, db path = stratoscan_probes).
# 3. Seed: 100 top + 20 random + real SPY (yfinance). Idempotent.
/usr/local/bin/python3.14 seed_probes_db.py
/usr/local/bin/python3.14 seed_probes_db.py --top 50 --random 25 --no-spy

# 4. Run the behavior probes against the probes DB (release gate R2).
#    RELEASE_GATE.md lists them; run_probes.py PROBES defines the set.
/usr/local/bin/python3.14 run_probes.py
/usr/local/bin/python3.14 run_probes.py --only verify_throughput.py verify_load_behavior.py
```

**Refresh strategy** — the probes DB is throwaway by design. To reset it,
re-run the seed (upserts real prod rows + SPY on top of whatever the probes
mangled). To get a pristine copy, drop and recreate `stratoscan_probes`, then
re-seed. Probes never write to prod or to `stratoscan_staging`.

**What the runner does**
- Loads `staging.env`, forces `DATABASE_URL` → `STAGING_DATABASE_URL` for every
  subprocess, and blanks Upstash creds (probes must stay in-memory isolated).
- Boots a dedicated rate-limited uvicorn via `probe_server_entry.py` for
  `verify_throughput` (which asserts 429s + headers). `probe_server_entry.py`
  enlarges ONLY the `read` bucket so the probe's ~1,200-request bench fits its
  documented budget assumption — the real 60/min burst is still verified by
  `verify_load_behavior` + the hermetic rate-limit suites.
- Refuses to run `verify_screen_cache_live` if port 8099 is occupied (the probe
  hardcodes it; a squatter would make it silently test the wrong server).
- Pre-warms the shared interpret cache via `/interpret` (not `/screen`) so cold
  real-LLM interprets never trip the probe's 20s client timeout, and so the
  `/screen` burst bucket (5/min) is never burned.

**Isolation caveat — the ONE intentional exception**: every probe runs with
`DATABASE_URL` forced to the probes DB and Upstash creds blanked. The single
probe that touches non-probes data is `verify_backtest_neon_lifecycle.py`,
which drives the **shared Neon backtest DB** (staging reuses prod's backtest
project). It is self-cleaning by design — probe-only symbols `PRBE`/`PRBENCH`,
count snapshots restored on exit — so no real backtest data is at risk, but if
you ever need 100% structural isolation for it, point `BACKTEST_DATABASE_URL`
at a staging-only backtest project.

## Migration dry-run on a copy of production (P1-6)

Before any release push, the exact `alembic upgrade head` the container
entrypoint runs on boot is replayed against a **throwaway copy of the
production schema + data** — so a broken migration is caught on the copy,
never at deploy time.

```bash
cd stratoscan

# prod (DATABASE_URL from .env) -> fresh scratch DB 'stratoscan_migrdry'
/usr/local/bin/python3.14 scripts/migration_dryrun.py

/usr/local/bin/python3.14 scripts/migration_dryrun.py --keep   # inspect the copy afterwards
/usr/local/bin/python3.14 scripts/migration_dryrun.py --target stratoscan_migrdry2
/usr/local/bin/python3.14 scripts/migration_dryrun.py --source-url "$STAGING_DATABASE_URL"  # dry-run against staging instead
```

**What it does** (exit 0 = safe to push, 1 = blocked, 2 = usage/env):

1. **Copy** every public table (schema rebuilt from `information_schema`;
   rows moved with binary `COPY`) into a scratch DB on the same Neon
   project's **direct** endpoint. Includes `alembic_version`, so the copy
   starts at prod's exact stamp. (Neon's pooler blocks `CREATE DATABASE ...
   WITH TEMPLATE` — hence the programmatic clone.)
2. **Snapshot** each table's row count + checksum (numeric-column sum + md5
   sample) before the upgrade.
3. **Run** `alembic upgrade head` against the copy.
4. **Verify** the head revision is stamped, every expected table/column
   exists, and no pre-existing table changed (row count AND checksum
   identical). Tables created by the migration are allowed; `alembic_version`
   is expected to move to head.
5. **Drop** the scratch DB (unless `--keep`).

**Safety invariants**
- Source is opened read-only (SELECT only).
- Target name must contain `migrdry` — refused otherwise.
- The copy is created on the direct endpoint of the same Neon project and
  dropped again at the end, so nothing persists unless `--keep`.

**Known state (Aug 2026):** production sits at `d5e6f7a8b9c0`, six revisions
behind head `b4c5d6e7f8a9` (stripe drop, screener-quality columns, job-monitor
tables, email-verification columns, admin role, `user_data`). All six are
idempotent (`ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`), so the
dry-run also proves they replay cleanly over prod's runtime-created
`job_runs`/`scheduler_heartbeat` tables that alembic never recorded.

## PITR restore drill (P1-4)

Neon offers Point-in-Time Recovery, but the restore path had never been
exercised end-to-end. The drill proves, repeatedly, that a restored copy of
the primary database serves real screens and backtests.

### The real restore (operator / Neon Console — a human step)

```
1. Neon Console → the project → Restore → point in time (e.g. 24h ago)
   → new branch/DB. OR: neonctl branches create ... --parent-timestamp <ts>
   (requires a NEON_API_KEY — not present on dev machines).
2. Rename the restored DB so the drill can identify it: the drill refuses
   any database whose name does not contain ``pitr`` (e.g. ``stratoscan_pitr``).
3. Grab its connection string (pooler endpoint is fine for reads).
```

### The automated drill (`scripts/pitr_restore_drill.py`)

```bash
cd stratoscan

# A) You already restored via the Console — verify THAT database:
/usr/local/bin/python3.14 scripts/pitr_restore_drill.py \
    --scratch-url postgresql://.../stratoscan_pitr?sslmode=require

# B) No console access — restore-equivalent: timed byte-faithful clone of
#    prod into a scratch DB, then the same verification:
/usr/local/bin/python3.14 scripts/pitr_restore_drill.py
/usr/local/bin/python3.14 scripts/pitr_restore_drill.py --symbol MSFT --start 2023-06-01
/usr/local/bin/python3.14 scripts/pitr_restore_drill.py --keep   # keep the copy
```

**What it verifies (exit 0 = pass, 1 = fail, 2 = usage):**

1. **Restore** — `--scratch-url` uses your real restored DB; default mode
   clones prod (schema via `information_schema`, rows via binary COPY — the
   same machinery as the P1-6 migration dry-run) and times it (~6s for the
   4,953-stock universe). The Console restore itself is documented above; the
   clone proves every downstream guarantee the restored DB must serve.
2. **Faithfulness** — the copy carries the full universe + users + the exact
   alembic stamp.
3. **Screens off the copy** — real `interpret → screen` pipeline (canned
   deterministic LLM filters, zero quota burn): a filtered screen returns
   real names (top: NVDA/AAPL/MSFT), a broad screen matches exactly the
   **screenable** universe (rows with price + market cap — the completeness
   guard's documented exclusion), and derived criteria (momentum) work.
4. **AAPL backtest + hand-verified equity** — real `run_backtest` on a real
   yfinance frame over a short window (so the curve is un-downsampled), with
   **every equity-curve point recomputed by hand** from the documented fill
   math (all-in, next-bar-open, fee+slippage, force-liquidate) and checked to
   <1.5%: engine `$92,925.91` vs hand `$92,852.15`, curve ends exactly at
   `final_value`, deterministic across two runs, plus a 10y sanity run.
5. **Read-only guarantee** — per-table checksums before vs after the drill
   are byte-identical: screens + backtests never mutate the restored copy.
6. **Timing** — every phase is timed and reported (last live run: **22.7s
   total**: clone 6.5s, checksum 2.3s, screens 8.8s, backtest 3.1s, verify 1.6s).
7. **Teardown** — the scratch DB is dropped unless `--keep`; a real
   `--scratch-url` DB is never modified.

**Live result (Aug 2026, default mode):** ✅ passed — 4,953 stocks cloned in
6.5s, 134-match tech screen + 4,899-row broad screen + 1,481-match momentum
screen off the copy, AAPL equity hand-verified (worst point 0.13%), 10y final
$683,194.66, restored DB byte-identical before/after.

## Deploying to Fly (staging app)

```bash
fly apps create stratoscan-staging
fly secrets set --app stratoscan-staging \
  DATABASE_URL=... FINNHUB_KEY_01=... ... RATE_LIMIT_ENABLED=false \
  JWT_SECRET=... NEWS_DATABASE_URL=... BACKTEST_DATABASE_URL=...
fly deploy -c fly.staging.toml
curl https://stratoscan-staging.fly.dev/health   # → 200 {"status":"ok",...}
```

## Verification checklist

- [ ] `staging.env` exists (gitignored) with `DATABASE_URL` → `stratoscan_staging`
- [ ] `staging.env.example` committed with all keys (secrets masked)
- [ ] `fly.staging.toml` exists (app `stratoscan-staging`)
- [ ] `python3 seed_staging.py` upserts real rows into staging stock_master
- [ ] `python3 run_staging.py --no-reload` serves `GET /health` → 200 on port 8010
- [ ] `RATE_LIMIT_ENABLED=false` confirmed (E2E/probes never 429)
- [ ] Screener returns results on staging (LLM interpreter + seeded data)
- [ ] `stratoscan_probes` exists; `STAGING_DATABASE_URL` points at it
- [ ] `python3 seed_probes_db.py` → **≥100 rows with AAPL + SPY** (verified live: 121)
- [ ] `python3 run_probes.py` → **every probe exits 0** (verified live)
- [ ] Probes DB isolated: prod row count unchanged; `stratoscan_staging` untouched

## Teardown

```bash
# Stop the staging server (find its PID on port 8010) and optionally drop the DB:
#   (direct endpoint) DROP DATABASE stratoscan_staging;
#   fly apps destroy stratoscan-staging
```

## Notes / issues encountered (P0-0 + P0-12)

- **Neon pooler + new database**: connecting to `stratoscan_staging` through the
  `-pooler` endpoint failed at first because the naive URL replace also mutated
  the username (`neondb_owner` → `stratoscan_staging_owner`). Fixed by swapping
  only the database path via `urlsplit`/`urlunsplit`. Both pooler and direct
  endpoints now reach the staging DB. Same discipline kept in `seed_probes_db.py`
  (it captures the PROD url before staging.env overrides `DATABASE_URL`).
- **CREATE DATABASE** must run on the **direct** (non-pooler) endpoint and with
  `autocommit=True` (can't run inside a transaction, and pgbouncer routes pooled
  connections to a single DB).
- **Probes DB seed vs staging env shadowing**: `seed_probes_db.py` originally
  read the production URL AFTER loading `staging.env`, so "prod" resolved to the
  50-row staging DB and the seed aborted at 51 rows. Fixed by capturing the prod
  URL first and passing it explicitly.
- **Freshness guard vs probe fixtures**: the screener's STALE_EXCLUSION_DAYS
  guard (added earlier) excludes rows without a fresh `updated_at`. The hermetic
  probes predate it, so their fabricated universes matched nothing (`[]`).
  Fixed in `verify_audit_behavior.py` / `verify_derived_fix.py` (stamp fixtures
  fresh) and `verify_full_backend_behavior.py` (GME is now EXCLUDED, not just
  flagged — assertions updated to `len(UNIVERSE)-1`).
- **Orphaned probe servers**: leftover uvicorns from a previous session on ports
  8099/63411 made `verify_screen_cache_live` silently test the WRONG server (its
  captured log had no "Screen request" lines). Killed the orphans; `run_probes.py`
  now refuses to run that probe while 8099 is occupied.
- **Throughput probe budget**: the probe's bench (~1,200 requests) assumes a
  ~2,000/hr read budget; the real `read` bucket is burst 60/min. Booted it
  against `probe_server_entry.py` which enlarges only the read bucket — the real
  burst is still covered by `verify_load_behavior` + the hermetic suites.
- `flyctl` is **not installed** on this machine, so `fly deploy -c fly.staging.toml`
  was not executed here — the config + secrets commands are documented above.
