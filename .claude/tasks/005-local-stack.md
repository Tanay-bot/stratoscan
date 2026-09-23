# 005 — One command brings up a production-shaped local stack

## Goal

From a clean checkout, `make up` starts the whole system arranged **the way production
is arranged**: API and scheduler as separate services, all three logical databases,
a Redis the app can actually talk to, and a mail catcher. `make down` destroys it,
`make rebuild` returns from zero.

The risk this removes is not downtime — there is no deployment yet. It is that the
local setup and the eventual production setup diverge quietly, so every difference
becomes a surprise on deploy day.

## Files

- `docker-compose.yml` *(new, repo root)*
- `Makefile` *(new, repo root)*
- `.env.local.example` *(new, repo root)*
- `scripts/initdb.sql` *(new — creates the three databases)*
- `.gitignore` *(add `.env.local` — one line)*

Nothing else. **Do not edit `stratoscan/docker-entrypoint.sh` or
`stratoscan/Dockerfile`** — the entrypoint was changed in the commit before this task
specifically to make this work, and it is already correct.

## Pattern

### Service 1 — `api`

Builds from `./stratoscan`. The image `ENTRYPOINT` is `docker-entrypoint.sh`, which now
dispatches on its first argument. Pass `command: api`. That runs Alembic migrations
through the **real** entrypoint retry loop and then uvicorn — exercising the same
migration path production uses, rather than running migrations by hand.

Needs a healthcheck on `GET /health` so the scheduler can wait for it.

### Service 2 — `scheduler` ← read this carefully

Same image, `command: scheduler`, and `depends_on: api: condition: service_healthy`.

The ordering is not cosmetic. Migrations are the API's job — the entrypoint deliberately
does **not** run them for the scheduler role, because two containers racing
`alembic upgrade head` against one database is a lock-contention bug. The scheduler must
therefore start after the schema exists.

**This service is the entire point of the day.** Until the commit before this task, the
entrypoint ignored its arguments and always ran uvicorn, so a scheduler service would
have silently started a *second API* — two healthy containers, zero schedulers. Your
Verify step below must prove that is not happening.

Exactly **one** scheduler replica, ever (golden rule #1). Do not add `deploy.replicas`.

### Service 3 — `postgres`

One Postgres 16 server hosting all three logical databases: `stratoscan`,
`stratoscan_backtest`, `stratoscan_news`. Create them with an init script mounted at
`/docker-entrypoint-initdb.d/initdb.sql`:

```sql
CREATE DATABASE stratoscan_backtest;
CREATE DATABASE stratoscan_news;
```

(The primary `stratoscan` database comes from `POSTGRES_DB`.) Give it a healthcheck
using `pg_isready` so the API waits for it. Named volume for the data so `make down`
can destroy it.

### Service 4 — Redis, and the trap in it

**`redis:alpine` will not work.** `src/redis_ratelimit.py` is not a RESP client — it
POSTs JSON to `/multi-exec` and `/pipeline` with an `Authorization: Bearer` header
(see `redis_ratelimit.py:79-116`). That is the **Upstash REST API**, which plain Redis
does not speak.

Use two services: `redis:7-alpine`, plus an Upstash-REST-compatible HTTP proxy in front
of it — `hiett/serverless-redis-http` is the standard one. Point
`UPSTASH_REDIS_REST_URL` at the proxy and set `UPSTASH_REDIS_REST_TOKEN` to whatever
token the proxy is configured with.

If you wire plain Redis instead, `get_rate_limit_store()` will fail open to the
in-memory store **by design**, the app will look completely healthy, and the Redis path
will be entirely untested. That silent success is exactly the outcome this task exists
to prevent.

### Service 5 — `mailpit`

`axllent/mailpit`. SMTP on 1025, web UI on 8025. Verification emails land there instead
of being sent. Set the app's mail settings to point at it.

### `.env.local.example`

Every key the stack needs, with local values — database URLs pointing at the `postgres`
service, the Upstash vars pointing at the proxy, `CORS_ALLOW_ORIGINS` set to an
**explicit** origin (`http://localhost:5173`), never `*`. Copy the structure and
comments style from `stratoscan/.env.example`.

### `Makefile`

```
make up        # build + start everything, wait until /health returns 200
make down      # stop and remove containers AND volumes (a true reset)
make rebuild   # down, then up, from zero
make logs      # follow all services
make ps        # what is running
make verify    # the checks in Verify below, in one command
```

`make down` must destroy volumes (`docker compose down -v`). You will run
`make rebuild` more than any other command over the next fifteen days; it is worthless
if it leaves state behind.

## Non-goals

- Do not modify `docker-entrypoint.sh` or `Dockerfile`. They are already correct.
- Do not change any application code under `stratoscan/src/`.
- Do not add a second scheduler replica or any process manager that starts the
  scheduler from inside the API container.
- Do not commit a real `.env.local` — only the `.example`.
- Do not add asset classes, services, or tools the plan did not ask for.
- No reformatting or drive-by cleanup.

## Baseline

Everything is green before you start — confirm it:

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

Prints nothing = **40/40**. This task adds no Python, so that number must be identical
when you finish.

## Verify

```bash
make rebuild                      # from zero, must exit 0

curl -fsS http://localhost:8000/health | head -c 200 ; echo
# expect status ok and database_alive true
```

**The assertion that matters — prove the scheduler is a scheduler:**

```bash
# The scheduler container must run the scheduler and NOT uvicorn.
docker compose exec -T scheduler ps ax | grep -c "src.main scheduler"   # expect >= 1
docker compose exec -T scheduler ps ax | grep -c "uvicorn"              # expect 0
```

If the second number is not 0 you have reproduced the exact bug this task was written
to avoid — stop and say so rather than working around it.

**Prove Redis is really being used, not silently bypassed:**

```bash
docker compose logs api | grep -iE "redis|rate.?limit store" | tail -5
# must NOT say it fell back to the in-memory store
```

**Then the rest:**

```bash
curl -fsS http://localhost:8025 >/dev/null && echo "mailpit up"
make down && make up              # teardown/rebuild cycle works twice
```

And unchanged Python:

```bash
cd stratoscan && export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

## Done

Commit and push branch `task/005-local-stack`. **Do not merge.** Claude reviews the diff
first — particularly the scheduler service and the Redis wiring.

## Prerequisite

This needs Docker with the Compose plugin (`docker compose version`). If it is not
installed, stop and say so — do not substitute a different orchestration tool.
