# Day 3 status, and the plan for the next session

> Written at the end of the session that built most of Day 3 directly (Claude, not
> Freebuff). Supersedes the task table in `DAY-03-PLAN.md`; the reasoning there stands.

## What shipped — `docs/claude-md`, 45/45 backend · 219/219 frontend

| Commit | What | Appendix A rows closed |
|---|---|---|
| `c920705` (008, Freebuff) | `token_version` + `POST /auth/logout-all` | log out everywhere; password changed on one device → other dies |
| `1fecffd` | Password reset + change password (backend) | reset requested; unknown address identical **incl. timing**; reuse rejected; expiry rejected w/ resend; only newest link valid; reset → all sessions revoked + notice |
| `8d172b6` | Forgot / reset pages, change-password + "sign out everywhere" in Settings | (makes the above reachable) |
| `afcecbf` | Account deletion — user, `user_data`, **and `backtest_jobs`** (no FK) in one tx | account deleted, data purged, confirmation sent |
| `1556a0e` | **Security fix: ADMIN_EMAILS escalation** | admin bootstrap can't be triggered by a squatter |
| `9019ddd` | Delete account in Settings | deletion reachable from the UI |
| `5b6acbc` | Sliding sessions `POST /auth/refresh` (30-day ceiling) + client auto-renew | token expires mid-session → refreshed on active use |
| `687c84b` | Idempotent verification links; resend-verification moved into the email budget | verification clicked twice; resend throttled |

### The finding that matters most

`1556a0e`. Anyone who registered the `ADMIN_EMAILS` address before the operator did got
`is_admin: true` in the register response — no inbox access needed — and the login gate
explicitly exempted listed addresses from `EMAIL_VERIFICATION_REQUIRED`. Reproduced
(`/admin/users` → 200, every user's email), fixed (verified address required), and the
test that literally asserted *"admin bypass must 200"* rewritten to run both directions.
Found by asking the plan's own review prompt of the code instead of assuming the answer.

### Verified beyond the unit suites

- Real **Postgres 16** (started locally in the VM): migration chain up/down, the reset
  single-use race (first `1`, second `None`), deletion across both tables, kept-token lookup.
- Real **HTTP**: uvicorn + Postgres + an SMTP sink. Known vs unknown reset: **352 ms vs
  351 ms**, same body. Token decoded from the delivered email resets the password; replay
  400; two pre-reset devices 200 → 401. (First attempt failed: the probe grepped raw
  quoted-printable MIME. The app was right; the probe wasn't.)
- **Mutation-checked**: unguarded single-use, no session revocation, unpadded unknown
  path (2 ms vs 205 ms — a 100× enumeration oracle), refresh resetting `auth_time`.
  Each caught.

## Day 3 — what's left

| Item | State | Recommendation |
|---|---|---|
| **Email change** | not built | Design below. ~Half a session. Security-sensitive → Claude, not Freebuff. |
| **Google OAuth** | not built | **Blocked on you**: needs a Google OAuth client ID + secret (free, console.cloud.google.com). Merge rule below. |
| Register with existing email → same response | **409 today** (leaks existence) | Decide with Day 6 — signup moves to *after* value there, which changes the register response anyway. Rate caps (10/day/IP, 50/day global) make it slow to exploit meanwhile. |
| Unverified user: backtest once, saving needs verification | undecided | Decide with Day 6: unverified accounts get the anonymous allowance; `PUT /me/personal` requires verification. |
| Fresh-agent security review | not run | **Run the plan's Day 3 review prompt in a session that did not write this code.** I built it; I inherit its blind spots. Paste the prompt from p.10 of the plan plus `git diff 1a0324d..687c84b`. |
| Deleted user's share links | deferred to Day 6 | Links die with the account. |
| Reset on production / Gmail inbox placement | deferred to Day 15 | Need a deployment + domain (SPF/DKIM/DMARC). |

### Email change — design, ready to build

- `POST /auth/change-email` (bearer) `{new_email, password}` → verify password (400 on
  wrong, never 401); if the new address is **taken, respond identically and send nothing**
  (no enumeration); else store `pending_email` + `pending_email_token_hash` +
  `pending_email_expires` (new migration), email a link to the **new** address, notify the
  **old** address that a change was requested. Put the route in the `auth_mail` scope.
- `POST /auth/change-email/confirm` `{token}` (no auth — the link may open elsewhere) →
  single `UPDATE users SET email = pending_email, … WHERE id AND pending_email_token_hash
  = %s`; also `UPDATE backtest_jobs SET user_email` in the same transaction (the async-job
  ownership check compares emails, `api.py` `snapshot.get("user_email") != user.get("email")`).
  Unique violation → 400 "that address is now in use".
- Interaction to test: changing *to* an `ADMIN_EMAILS` address grants admin on next login
  — correct only because the new address had to be verified. Pin it.

### Google OAuth — the merge rule (the classic bug)

Google says `alice@gmail.com`, verified. A password account for that address exists:

- **verified** → link the Google identity to it; sign in.
- **unverified** → this is the squatter case. Someone registered Alice's address without
  her inbox. **Do not merge into it as-is**: the squatter's password would keep working on
  Alice's account. Take the row over for Alice: make the password unusable, bump
  `token_version` (kills the squatter's sessions), mark verified. Test exactly this.
- none → create a verified account with no usable password.

## Heavy hitters for the next session, in order

**1. Day 5 — the first sixty seconds.** Highest user-visible impact in the plan: a
first-timer currently hits a synchronous endpoint with a *20-minute* client timeout. Make
the async path the UI default, drive real progress from `backtest_jobs.phase` /
`done_symbols` ("Fetching history, 3 of 5"), default a first run to 5 symbols, stream the
first symbol's result, add a duration-ceiling test, and a yfinance-timeout failure drill.
**Profile before touching `BACKTEST_PARALLEL_WORKERS`** — it is 4 against a pool of 3, and
it is the constant most likely to move a golden number (golden rule #6).

**2. Day 6 — no signup wall + share links.** The biggest funnel lever, and it settles two
open Day 3 questions (register enumeration, unverified allowance). Anonymous token →
2–3 backtests → claim results on signup; `GET /r/{id}` public, unguessable, opt-in,
revocable, **no user data in the payload** (assert it). Needs its own tight rate-limit
scope for anonymous traffic.

**3. Day 4 — instrumentation + legal floor.** Must exist before any user arrives: an
`events` table with one `track()` entry point and the plan's 15 events (`second_backtest`
is the kill criterion); backend error capture; the disclaimer *next to the number*, not in
the footer.

**4. Finish Day 3**: email change (Claude), then OAuth once you have the client ID.

### What to hand Freebuff (cheap, well-specified, not security-critical)

- Day 4's `events` table + `track()` + wiring the 15 call sites — highly spec-able.
- A Settings test for the change-password token swap (the logic is there; the test isn't).
- De-duplicate the route pin (`test_api_smoke` hand-lists routes that `openapi.json`
  already pins) — same "one owner per fact" fix as the Alembic head.

### Budget note

Everything above ran on the free local stack plus this VM's Postgres. Nothing needs a
paid service until deployment. OAuth needs a (free) Google client; email deliverability
needs a domain.
