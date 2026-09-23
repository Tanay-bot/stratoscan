# Review — `task/005-local-stack` @ `5e9bcbd`

**Verdict: accept, after one change.** The stack is correct, the two traps the spec
called out were both handled properly, and the four contradictions the report raised are
all real — I checked each against the source rather than taking them on trust. One defect
was missed by both of us, and it is the same species of bug the whole task exists to
prevent: something that looks healthy and is not.

## What I verified independently

| Check | Result |
|---|---|
| Files changed | 5, exactly the spec's list — no `src/`, no `Dockerfile`, no entrypoint |
| Backend suites | 40/40, unchanged (no Python in the diff) |
| `docker compose config -q` | exit 0, Compose v5.1.1 |
| YAML merge key `<<: *app` | resolves — both roles share one build and one image tag |
| `deploy.replicas` anywhere | absent (golden rule #1 holds) |

## The four contradictions — all confirmed

1. **`ps` is absent from the image.** The Dockerfile is `FROM python:3.14-slim` and runs
   no `apt-get`. Debian slim excludes `procps`. The spec's `ps ax` lines could not have
   worked; substituting `/proc/1/cmdline` was right, and it is the *better* check anyway,
   because the entrypoint `exec`s — so PID 1 is the workload itself, not a parent shell.
   Spec 005 has been corrected.

2. **mailpit is inert.** `src/emails.py:41` hardcodes
   `SENDGRID_MAIL_URL = "https://api.sendgrid.com/v3/mail/send"`, and `grep -rn "smtp"
   src/` returns nothing at all. Wiring it would have meant editing `src/`, which the
   non-goals forbid. Running the service and documenting the gap in two places was the
   correct call; refusing to silently paper over it is the whole point. The spec's own
   wording was wrong here and has been corrected.

3. **`FINNHUB_KEY_01` is a boot prerequisite.** `scheduler.py:85` —
   `self._pool = pool or initialize_client_pool()` in the constructor — and
   `initialize_client_pool` raises `ValueError` on an empty key list. Without a key the
   scheduler dies immediately, which would have made the day's one load-bearing assertion
   unrunnable. Correctly diagnosed and documented.

4. **`get_rate_limit_store()` caches for process life.** `redis_ratelimit.py:411-439`:
   `_store_resolved` is set once and returned on every later call. So if the proxy is not
   answering at first resolution, the app pins the in-memory store permanently and logs a
   warning nobody reads. This makes `depends_on: redis-http: condition: service_healthy`
   genuinely load-bearing rather than decorative. Good catch, correctly wired.

The two self-reported holes in `make verify` — grepping for absence passing on empty
logs, and `/health` being display-only — were both real and both are properly closed in
`5e9bcbd`. The Redis grep is safe: only two emitted log lines in the whole codebase
contain "in-memory", and both are genuine failure paths.

## The defect — API goes unhealthy after 83 minutes

`.env.local.example` ships `RATE_LIMIT_BYPASS_IPS=""` with the limiter on and
`RATE_LIMIT_PER_DAY="1000"`.

The API container's own Docker healthcheck polls `http://127.0.0.1:8000/health` every 5
seconds. `_scope_for_path("/health")` returns `read` — there is no exemption for health
or metrics paths. So the healthcheck spends the `read` daily budget on itself:

```
first 429 at request # 1001
docker healthcheck interval 5s -> minutes until unhealthy: 83.4
```

(Measured against the real app with `TestClient`, burst raised to isolate the daily
window. Not arithmetic.)

At that point `urlopen` raises `HTTPError`, the healthcheck fails three times, and the
container is marked **unhealthy while serving every request correctly**. The scheduler,
already started, keeps running — so the damage is not an outage. It is worse for this
project's purposes: `docker compose ps` starts lying, and the next `make up` against a
running stack blocks on `depends_on: api: condition: service_healthy`. A stack that
reports a false red after an hour and a half will train you to ignore it, which is
exactly the instinct fifteen days of work cannot afford.

### The fix — one line

In `.env.local.example`:

```diff
-RATE_LIMIT_BYPASS_IPS=""
+# The container's own healthcheck polls /health every 5s from loopback, and /health
+# is in the `read` bucket (1,000/day). Unexempted, the probe exhausts that budget in
+# ~83 minutes and the container then reports unhealthy while working perfectly.
+# Exempting loopback does NOT weaken what this stack exists to test: the bypass is
+# checked before the store call, but `make up` / `make verify` curl from the host,
+# which arrives as the bridge gateway address, so the Redis path is still resolved
+# and still asserted.
+RATE_LIMIT_BYPASS_IPS="127.0.0.1"
```

Verified both halves: with the address bypassed, 1,200 consecutive `/health` requests all
return 200; and `_canonical_ip("::ffff:127.0.0.1")` collapses to `127.0.0.1`, so the
IPv6-mapped form the container may present matches the same entry.

## Two smaller notes

- **The scheduler healthcheck is looser than the Makefile check.**
  `grep -qa scheduler /proc/1/cmdline` also matches `sh ./docker-entrypoint.sh scheduler`
  — i.e. it would pass if the entrypoint stopped `exec`ing. `make verify` greps
  `src.main scheduler`, which does not. Match the healthcheck to it:

  ```diff
  -      test: ["CMD-SHELL", "grep -qa scheduler /proc/1/cmdline && ! grep -qa uvicorn /proc/1/cmdline"]
  +      test: ["CMD-SHELL", "grep -qa src.main /proc/1/cmdline && ! grep -qa uvicorn /proc/1/cmdline"]
  ```

- **The pruning pass is not wanted.** The report offered to cut the comment volume in
  `docker-compose.yml` and `.env.local.example`. Leave them. Every long comment in both
  files is attached to a trap that has already cost someone time — the REST-vs-RESP
  shim, the cached store resolution, the inherited port-8000 probe, the SendGrid gap.
  That is the house style (see `CLAUDE.md`, `stratoscan/.env.example`), and prose that
  explains a landmine is the cheapest thing in the repository.

## Merge

Apply the `RATE_LIMIT_BYPASS_IPS` change and the healthcheck one-liner, push, and this
merges as-is. Nothing else needs to change.
