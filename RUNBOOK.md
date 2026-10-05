# Launch runbook

> Written on Day 15 (2026-10-05), while every detail was fresh, from a dress rehearsal on a
> local production-shaped stack. Use it whenever you decide to go live. **Mechanics** (commands,
> secrets, first deploy) live in `DEPLOY.md`; the **scheduler** in `stratoscan/SCHEDULER.md`;
> the **release gate** in `stratoscan/RELEASE_GATE.md`. When a workflow and a document disagree,
> the workflow wins.

---

## 1. What the rehearsal proved (2026-10-05)

All on the local compose stack (`docker-compose.yml`: one image, API + scheduler as separate
containers, Postgres with three databases, Upstash-compatible Redis, a mailbox).

| Drill | Result | Number |
|---|---|---|
| **Cold start from zero** — fresh clone → `make rebuild` (volumes deleted, image built, migrations through the real entrypoint, ETF seed, scheduler started) → `/health` ok + database alive | ✅ | **66 s** (a code-only redeploy: 21 s) |
| **Backup and restore**, all three databases — `scripts/backup_restore_drill.py`: `pg_dump -Fc`, restore into a scratch database, row count + content fingerprint of every table | ✅ identical | main 17 tables (0.1 s / 0.2 s), backtest 280,500 bars (10.4 MB; dump 1.5 s, restore 1.0 s), news 2,000 articles. The fingerprint catches one altered price with row counts unchanged. |
| **Rollback** — `scripts/rollback_drill.sh`: build a deliberately broken image, deploy it, prove the bad build is what's failing (its own crash in the logs), redeploy the last good image | ✅ | detected **2.3 s** after deploy; healthy **9.5 s** after the bad deploy (target < 300 s) |
| **Exactly one scheduler** | ✅ after a fix | see below |

**Exactly one scheduler — checked, then checked again.** The first check failed: a second
`docker compose run scheduler` started silently and both ran every job — the failure that
doubles cost while looking normal. Fixed the same day with a lease (`scheduler_heartbeat.owner`,
`src/job_monitor.py`), then rehearsed live:

- a second scheduler → refused before its first job, exit 3, "Second scheduler refused" alert;
- the same container restarted → re-claims its own lease at once;
- `docker stop` (SIGTERM) → lease released; a brand-new container claims it immediately;
- an **unclean** stop (the pre-fix image) → the replacement is refused for up to 45 minutes;
  `SCHEDULER_TAKEOVER=true` (or `scheduler --takeover`) recovered it. Expect this after a crash.

**Two production bugs the rehearsal found that no local test could have:**

1. `fly.toml` had **no scheduler process** — the entrypoint starts only the API — so production
   would have run no data jobs and every screen would have emptied after seven days. Fixed: two
   process groups (`app`, `scheduler`).
2. `deploy.yml` rolled back with **`fly rollback`, which does not exist** — flyctl rolls back by
   redeploying an older image ([Fly rollback guide](https://fly.io/docs/blueprints/rollback-guide/)).
   Fixed: images are labelled `sha-<commit>`, a failed deploy redeploys the last green commit's.

### The full gate, same day

| Gate | Result |
|---|---|
| Backend hermetic suites | ✅ 63 / 63 |
| Golden set + hand-computed accuracy + trade P&L probes | ✅ |
| Failure drills 1–7 | ✅ 7 / 7 (drill 2 was passing without testing anything — the free parser and a 401 meant it never reached the dead model; fixed) |
| Accuracy loop | ✅ 3,503 / 3,503 checks on 49 results — **offline stack**: synthetic prices, no model, so 10 of 30 queries not run (reproduced the published figure exactly) |
| End-to-end journeys (4) + accessibility + keyboard | ✅ 15 / 15 |
| Frontend: typecheck, lint, format, unit tests, build, header guard, demo-mode guard | ✅ (a production build with `VITE_DEMO_MODE=true` is refused) |
| **Release-gate probes (19) against staging** | ❌ **not run** — needs `staging.env` and the staging services, which the rehearsal machine does not have |
| **Real market data loaded** | ❌ **not done** — Finnhub and Yahoo were unreachable from the rehearsal machine; the cold start used seeded data |

Those two ❌ are the first things to do on any machine that has the keys.

---

## 2. Exists only in production — cannot be tested before going live

| Item | Why it matters | How you'll know |
|---|---|---|
| **Market-data licence** | Finnhub's plans — free *and* paid — are licensed for personal use; yfinance is an unofficial Yahoo client. A public, monetised product needs a commercial licence for quotes, fundamentals and history. **This is the launch blocker, not an engineering task.** | A signed licence, then swap the data source behind `data_fetcher` / `fetch_historical_data`. |
| Domain, DNS, TLS | Everything else waits on propagation. | `curl -I https://<domain>` → 200 with a valid certificate; `fly certs show`. |
| Email authentication (SPF, DKIM, DMARC) | Without it verification and reset mail lands in spam; no local test reveals it. | Send to a Gmail and an Outlook address; "Show original" → SPF/DKIM/DMARC all `PASS`. |
| Production secrets as real secrets | Never a file. | `fly secrets list` shows every name in `.env.example` that production needs. |
| Managed database backups | Neon's point-in-time restore is the backup; it has never been restored. | Restore a branch to a timestamp, then run `scripts/backup_restore_drill.py --restore-server <scratch Neon project>` against it. |
| Managed Redis | Exact cross-machine rate limits. | `/health` stays ok and `verify_upstash_ratelimit.py` passes against it. |
| External uptime monitoring | By definition it cannot run from inside. | A third-party monitor on `/health` paging the alert channel; kill the API once to prove it. |
| Security headers on the live host | `npm run verify:headers` checks the config, not the host. | `curl -sI https://<domain> \| grep -i -E "content-security-policy\|strict-transport"`. |
| Cold-start latency of a suspended Neon database | Local Postgres never sleeps. | Leave it idle 10 min, time the first `/health` and the first screen; compare with the numbers above. |
| Google sign-in redirect URIs | Registered per domain. | One full "Continue with Google" round trip on the real domain. |
| Payment provider | Only when you take money (`stratoscan/BILLING.md` lists what an adapter needs). | `tests/billing_conformance.py` against the adapter. |

---

## 3. A week before

- [ ] Buy the domain and point DNS (nothing else goes first — propagation).
- [ ] Sign the market-data licence (§2). Without it, do not launch publicly.
- [ ] Create the Fly app, three Neon projects, Upstash Redis, SendGrid sender, Vercel project,
      Sentry project; note what each requires (DEPLOY.md lists the secrets).
- [ ] Add SPF, DKIM and DMARC records; wait until they verify.
- [ ] Price it out for a month (§7) and confirm you are willing to spend it.
- [ ] Run the release gate against staging: `cd stratoscan && scripts/run_release_gate.sh --report gate.md`.
- [ ] Re-run the accuracy loop against real prices with a model configured; put the date and
      figure in README.md and on `/verification` (the page and `tests/test_public_claims.py`
      must change together).

## 4. Deploy day, in order

1. **Database.** `fly deploy` runs `alembic upgrade head` through the real entrypoint (retried
   ×5 for a cold Neon). Confirm: `fly logs -a stratoscan-api | grep -i alembic` → no errors.
2. **API.** `/health` → 200 with `database_alive: true`; `/meta` → 200; `/docs`, `/redoc`,
   `/openapi.json` → 404 (golden rule 9).
3. **Scheduler — exactly one.** `fly scale count app=1 scheduler=1 -a stratoscan-api`. Then
   check, and check again:
   - `fly status -a stratoscan-api` → one `scheduler` machine, started;
   - `GET /ops/jobs` (admin token) → `scheduler.alive: true`, `scheduler.lease_owner` = that
     machine's id;
   - a second scheduler would be refused — don't test that on production; the rehearsal did.
4. **Frontend.** Deploy; confirm the demo banner is absent (`VITE_DEMO_MODE` off — the build
   refuses to ship with it on) and that the app talks to the real API.
5. **One full refresh.** Watch `job_runs` / `/ops/jobs`: `price_refresh` succeeds and
   `stock_master.updated_at` advances. Run `python -m src.main backfill-volume` after the first
   cache warm.
6. **Headers.** Re-verify against the live host (§2).
7. **Alerts.** Point `ALERT_WEBHOOK_URL` at the production channel; stop the scheduler machine
   for an hour and confirm the stale-heartbeat alert arrives; start it again.
8. **Uptime monitoring.** Connect the external monitor.
9. **Email.** Sign up with a real address; the verification email reaches an inbox, not spam.
10. **Cold start.** Measure a suspended-database first request; write the number here.
11. **Full gate:** release-gate probes against staging, the seven drills, golden set, accuracy
    loop against production (`ACC_BASE=https://<api host> python accuracy_loop.py`). The
    automated end-to-end journeys read mail from the local stack's sink, so on production walk
    the same four by hand: a trial run and share link, a new account through to a saved
    strategy, a password reset, an admin plan change.
12. **Rehearse a rollback on the live stack** before anyone uses it (§5) and time it.

## 5. Rolling back

There is no `fly rollback`. A rollback is a deploy of an older image:

```bash
fly releases -a stratoscan-api --image          # find the last good image
fly deploy -a stratoscan-api --image registry.fly.io/stratoscan-api:sha-<commit> \
           --strategy immediate -y
```

`deploy.yml` does this automatically when `/health` never comes up (images deployed before
Day 15 carry no `sha-` label — the first rollback after Day 15 may need the manual form).
Frontend: Vercel → Deployments → the previous one → "Promote to Production".

**The image rolls back; the database does not.** Every migration so far is additive
(`ADD COLUMN IF NOT EXISTS`, new tables), so an older image runs on a newer schema. Before
shipping a migration that drops or renames anything, write down how the previous image
survives it — or don't ship it in the same release as code that depends on it.

## 6. Incident procedure

**Check, in this order:**

1. **The scheduler heartbeat** — `GET /ops/jobs`. Most silent problems are stale data, and stale
   data looks like a product bug rather than an outage. `alive: false` or stale jobs → the
   scheduler (SCHEDULER.md §5). Refused with "Second scheduler refused"? Find the other one
   before forcing a takeover.
2. **The error tracker before the code** — `GET /admin/errors` (scrubbed server traces, route
   template + exception type) and Sentry for the frontend. The stack trace is faster than
   intuition.
3. **Your own shields** — a wave of 429s may be the global buckets (`screener_llm` 900/day,
   `backtest_llm` 200/day, `explain_llm` 300/day, `auth_register` 50/day); they are
   planet-wide by design, so one enthusiastic user can exhaust them for everyone. Raising them
   is a cost decision, not a fix.
4. **The database** — 503 + `Retry-After` responses mean the database is unreachable or the pool
   is waiting (never a 500, never a sign-out); check Neon's status and compute.

**Then:**

- **Roll back rather than fix forward** (§5). You rehearsed it: 9.5 s locally. Fix on a branch,
  calmly, afterwards.
- **Tell users something true.** A short honest note beats silence; this is a product whose whole
  pitch is honesty about numbers. Template:

  > *Since {time} UTC, {what people saw — e.g. "screens returned no results" / "backtests
  > failed"}. Cause: {one plain sentence}. {Fixed at {time} / We rolled back at {time} and are
  > fixing it}. {If numbers were affected: "Results run between {t1} and {t2} may be wrong — re-run
  > them."} Nothing about your account or saved strategies was lost.*

- Write two lines in `BUILD_LOG.md`: what broke, what you changed so it can't again.

## 7. Monthly cost at launch scale

Assumptions: a few hundred to ~1,000 registered users, one API machine + one scheduler machine,
three small Neon databases, prices as of October 2026 (US region, before tax).

| Item | Choice | Est. / month | Note |
|---|---|---|---|
| **Market data (licensed)** | Commercial licence for quotes + fundamentals + EOD history | **$50 – $500+** | The unknown that dominates. Finnhub's $50–$200 plans are personal-use; commercial/redistribution is custom-priced. EOD-only providers start around €20–€100/mo but check redistribution terms. **Get a quote.** |
| API hosting | Fly.io, shared-cpu-1x 512 MB, always on | ~$3.20 | 256 MB is $1.94; bump to 512 MB if the nightly warm OOMs |
| Scheduler hosting | Fly.io, shared-cpu-1x 512 MB | ~$3.20 | the machine the old config never had |
| Databases | Neon Launch (usage-based), three projects | ~$10 – $25 | ~$0.106 per CU-hour + $0.35/GB-month. The 15-minute price refresh keeps the main DB awake roughly a third of the time at 0.25 CU (~$6); backtest history for the full universe is a few GB (~$1–2) |
| Rate-limit store | Upstash Redis pay-as-you-go | ~$0 – $5 | $0.20 per 100K commands; ~1 command batch per request |
| Email | SendGrid Essentials | ~$20 | the free tier's ~100/day is the first limit hit at launch (LOAD.md); verification + reset + weekly digests |
| Frontend hosting | Vercel Pro | $20 | Hobby is non-commercial |
| Language models | free tiers (Z.ai, Gemini, Groq) | $0 | global buckets cap usage; ~80% of queries never call a model |
| Error tracking | Sentry free developer tier | $0 | |
| Domain | | ~$1 | ~$12/year |
| **Total without data** | | **≈ $60 – $95** | |
| **Total with data** | | **≈ $110 – $600+** | the licence decides |

Sources: [Fly.io pricing](https://fly.io/docs/about/pricing/), [Neon pricing](https://neon.com/pricing),
[Upstash Redis pricing](https://upstash.com/pricing/redis), [Finnhub pricing](https://www.finnhub.io/pricing-stock-api-market-data),
[EODHD pricing (G2)](https://www.g2.com/products/eodhd-financial-data-apis/pricing),
[SendGrid pricing explained](https://automationatlas.io/answers/sendgrid-pricing-explained-2026/),
[Vercel pricing 2026](https://modelence.com/compare/vercel-pricing). Re-check before signing anything.

## 8. Five people, in the room — not yet done

The plan's last Day 15 step is human and has not happened. When it does, one at a time:

- Traders, investors, aspiring investors, anyone who has asked you about markets.
- Open the landing page, say "find out whether an idea of yours has ever worked", then **say
  nothing**. Don't explain, don't rescue, don't point. Every rescue destroys the data.
- Write down every moment you wanted to lean over: what they did, what they expected, where they
  stopped. Note the time to first result and whether they found the rules, the explanation and
  the "how we check the numbers" page on their own.
- Afterwards, one question: **"What did you expect to happen that didn't?"**
- Fix nothing that night. Patterns across five people beat a fast fix for one. Put the notes in
  `BUILD_LOG.md`.

## 9. After launch

The plan's gates count from launch day, not from today: day 90 (200+ users, 25%+ running a
second backtest → turn on billing), month 6 (10 paying customers → licensed data, Studio tier),
month 12 ($500+ MRR, < 10% churn). Kill criterion, written down in advance: if several hundred
people try it and fewer than 5% ever run a second backtest, the need is not what the plan assumed.
The funnel numbers for all of these are in `GET /admin/events`.
