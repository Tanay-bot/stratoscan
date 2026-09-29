# 009 — "Continue with Google"

## Goal

A user clicks **Continue with Google** on the login or signup page, picks their Google
account, and lands in the app signed in, with no password to create. It is the
highest-conversion item in the Day 3 plan.

It is also the easiest auth feature to make dangerous, in one specific place: **what
happens when the Google email matches an account that already exists.** Read "The merge
rule" twice. The rest is plumbing.

## ⚠️ Keys — read before anything else

**Never ask for, paste, print, log, or commit the Google client secret.** Use empty
placeholders everywhere. The owner puts the real values into their own `stratoscan/.env` and
`.env.local` with a terminal command; you never see them. Tests use fake values. If you find
a real-looking secret in any file you are about to commit, stop and say so.

## Files

- `stratoscan/src/config.py` — three settings
- `stratoscan/.env.example`, `.env.local.example` — document them, **empty values**
- `stratoscan/alembic/versions/e3f4a5b6c7d8_add_google_sub.py` — new, `down_revision = "d2e3f4a5b6c7"`
- `stratoscan/src/database.py` — column, idempotent backfill, four methods
- `stratoscan/src/google_oauth.py` — **new**; all Google-specific logic lives here
- `stratoscan/src/api.py` — two routes, and nothing else
- `stratoscan/tests/fakes.py` — mirror the four DB methods + the new key on stored users
- `stratoscan/tests/test_google_oauth.py` — **new**
- `stratoscan/tests/test_api_smoke.py` — add the two routes to the pinned route set
- `stratoscan/openapi.json`, `openapi.pretty.json` — regenerate (`python scripts/regen_openapi.py`)
- `stratoscan/FRONTEND_API.md`, `CLAUDE.md` (route table §6) — document the two routes
- `frontend/src/lib/api/client.ts` — export the API origin (see Pattern)
- `frontend/src/pages/Auth.tsx` — the button
- `frontend/src/pages/GoogleComplete.tsx` — **new**
- `frontend/src/App.tsx` — one route
- `frontend/src/pages/GoogleComplete.test.tsx` — **new**

Nothing else. Files that fail *because of* your additions (a pin you could not have known
about) may change too, but say which and why, as you did in 008.

## Pattern

### Settings (`config.py`)

```python
google_client_id: str = ""
google_client_secret: str = ""
google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
```

Add a property `has_google_oauth` → `bool(self.google_client_id and self.google_client_secret)`.
The redirect URI must match what the owner registered in Google Cloud **exactly** — it is
`http://localhost:8000/auth/google/callback`. Do not change it.

### The flow — server-side authorization code, stdlib only

**No new dependencies.** `requirements.txt` and `requirements-lock.txt` must be
byte-identical when you finish. Use `urllib`, `secrets`, `hmac`, `json`, `base64` — the same
approach `emails.py` and `redis_ratelimit.py` already take.

**`GET /auth/google/start`**

1. If `not settings.has_google_oauth` → `302` to `{APP_BASE_URL}/login?oauth_error=unavailable`.
2. Generate `state = secrets.token_urlsafe(32)` and `nonce = secrets.token_urlsafe(32)`.
3. Put both in **one cookie** named `g_oauth`: `HttpOnly`, `SameSite=Lax`, `Path=/auth/google`,
   `Max-Age=600`, `Secure` only when `settings.is_production`. Sign the cookie value with
   HMAC-SHA256 over `settings.jwt_secret` so it cannot be forged; verify with
   `hmac.compare_digest`.
4. `302` to `https://accounts.google.com/o/oauth2/v2/auth` with query params `client_id`,
   `redirect_uri`, `response_type=code`, `scope=openid email profile`, `state`, `nonce`,
   `prompt=select_account`. Build it with `urllib.parse.urlencode`.

**`GET /auth/google/callback?code=…&state=…`**

1. Read and verify the `g_oauth` cookie. Missing, bad signature, or `state` not equal
   (`compare_digest`) → error redirect. **This is the CSRF defence; it is not optional.**
2. Delete the cookie on every outcome, success or failure.
3. Exchange `code` at `https://oauth2.googleapis.com/token` (POST, form-encoded, with
   `client_id`, `client_secret`, `redirect_uri`, `grant_type=authorization_code`). Put this
   network call in a module-level function in `google_oauth.py`, e.g.
   `exchange_code(code) -> dict`, **so tests can patch it**.
4. Take `id_token` from the response and validate its claims in a pure function
   `validate_id_token(id_token, nonce) -> dict` (raises on any failure):
   - decode the payload (middle segment, base64url); you may skip the RS256 signature
     check **only because** the token came straight from Google's token endpoint over TLS in
     the step above — OpenID Connect Core §3.1.3.7 allows exactly this. Say so in a comment.
     Never accept an `id_token` from the browser.
   - `iss` ∈ `{"accounts.google.com", "https://accounts.google.com"}`
   - `aud == settings.google_client_id`
   - `exp` in the future
   - `nonce` equals the cookie's nonce
   - **`email_verified is True`** — if Google has not verified the address, reject. This
     is load-bearing for the merge rule.
5. Apply **the merge rule** (next section) → a user row.
6. Apply the existing admin bootstrap:
   `_bootstrap_admin_role(db, email, user_id, is_admin, True)` (the address is verified).
7. Mint `create_access_token(user_id, email, token_version)` and `302` to
   `{APP_BASE_URL}/auth/google/complete#token=<jwt>&expires_in=<seconds>`.
   **The token goes in the fragment (`#`), never the query string (`?`)** — fragments are
   not sent to servers or written to access logs.

**Every failure** → `302` to `{APP_BASE_URL}/login?oauth_error=<code>` where `<code>` is one
of `unavailable`, `state`, `exchange`, `token`, `unverified`, `failed`. Log the real reason
with `logger.warning`; never put exception text in the redirect (golden rule #12).

Both routes land in the `auth` rate-limit scope by prefix already. **Do not touch
`RATE_LIMITS` or `_scope_for_path`.**

### The merge rule — the part that matters

A Google identity is `sub` (stable forever) plus `email`. Store `sub` on the user in a new
column `google_sub TEXT UNIQUE`. Resolve in this order:

| Case | Do this |
|---|---|
| A user already has this `google_sub` | Sign them in. (Even if their Google email changed since.) |
| A user has this email **and it is verified** | Link: set `google_sub` on that row. Sign in. Their password keeps working. |
| A user has this email **and it is NOT verified** | **Take the row over** — see below. |
| No user | Create one: `email_verified = True`, `google_sub` set, password = an **unusable** hash. |

**Why the unverified case is special.** An unverified account is one whose inbox was never
proved. Anyone can register `alice@gmail.com` without owning it. If Alice later signs in
with Google and you simply link, the stranger's password now opens Alice's account, and
their existing session stays alive. That is an account takeover, and it is the classic bug
in this feature.

The takeover, in **one SQL statement**:
- set `password_hash` to an unusable hash — the stranger's password stops working
- set `google_sub`, set `email_verified = TRUE`
- clear `verification_token`, `verification_token_expires`, `reset_token_hash`, `reset_token_expires`
- `token_version = token_version + 1` — every session the stranger holds dies

**The unusable password:** `hash_password(secrets.token_urlsafe(32))` — a real PBKDF2 hash of
a random value nobody knows. Do **not** use a marker string like `"!"`: `verify_password`
would reject it faster than a real hash, and that timing difference would reveal which
accounts are Google-only. A Google-only user who wants a password uses "Forgot password".

DB methods (`PostgresDB` + `FakeUserDB`, same signatures, same returns):

```python
get_user_by_google_sub(sub) -> Optional[dict]        # id, email, is_admin, token_version
link_google_identity(user_id, sub) -> None
take_over_for_google(user_id, sub, unusable_hash) -> int   # returns new token_version
create_google_user(email, sub, unusable_hash) -> int       # returns id
```

The column and backfill follow `USERS_PASSWORD_RESET_BACKFILL` exactly (idempotent `ADD
COLUMN IF NOT EXISTS`, plus a unique index `CREATE UNIQUE INDEX IF NOT EXISTS
idx_users_google_sub ON users (google_sub)`), wired into `init_schema()`, **and** the Alembic
revision. Both, not either.

### Frontend

**`client.ts`** — factor the origin out of `ADMIN_DASHBOARD_URL` and export it, so the
button and the admin link agree:

```ts
export const API_ORIGIN = (API_URL.startsWith('http') ? API_URL : 'http://localhost:8000').replace(/\/+$/, '')
export const ADMIN_DASHBOARD_URL = `${API_ORIGIN}/admin`
```

**`Auth.tsx`** — a **Continue with Google** button in both login and signup modes, above the
email form, with an "or" divider. It is a plain link (`<a href={`${API_ORIGIN}/auth/google/start`}>`),
not an axios call — the browser must navigate to Google. Hide it in demo mode. If the URL has
`?oauth_error=…`, show one friendly message (map each code to a sentence; `unverified` →
"Your Google account's email isn't verified").

**`GoogleComplete.tsx`** at route `/auth/google/complete` (public):
1. Read `token` and `expires_in` from `window.location.hash`.
2. **Immediately** remove the fragment: `window.history.replaceState(null, '', window.location.pathname)`.
3. Store the token (`setToken`), fetch the user with `services.me()`, then call
   `useAuth.getState().setSession({access_token, token_type: 'bearer', expires_in, user, email_sent: false})`.
4. Navigate to `/app`. On any failure → `/login?oauth_error=failed`.

## Non-goals

- No new Python or npm dependency. No OAuth library.
- Do not change login, register, reset, the admin bootstrap logic, rate limits, or any
  existing route's behaviour.
- No other providers (GitHub, Apple). No "unlink Google" UI.
- Do not accept an ID token posted from the browser. Server-side code exchange only.
- No reformatting or drive-by cleanup.

## Baseline

```bash
cd stratoscan && export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"; done
cd ../frontend && npx vitest run 2>&1 | grep "Tests "
```

Measure it; don't trust a number here (see `knowledge.md` §2). At the time of writing:
**45/45** backend, **219/219** frontend. Targets: **46/46** and **≥222**.

## Verify

### `tests/test_google_oauth.py` — hermetic, patch `exchange_code`

Pin every one of these. Each rejection has an acceptance next to it on purpose:

1. `/auth/google/start` configured → 302 to `accounts.google.com`, URL carries `client_id`,
   the exact `redirect_uri`, `state`, `nonce`, `scope=openid email profile`; sets `g_oauth`
   cookie with `HttpOnly` and `Path=/auth/google`.
2. Not configured → 302 to `/login?oauth_error=unavailable`.
3. Callback with no cookie / tampered cookie / state mismatch → `oauth_error=state`, **no user created**.
4. New Google user → account created, verified, `google_sub` stored; redirect `Location`
   contains `#token=` and **does not contain `?token=`**; that token → `GET /auth/me` 200.
5. Returning user by `sub` whose Google email has changed → signs into the **same** user id.
6. Existing **verified** password account → linked, same id, **its password still logs in**.
7. **THE ONE THAT MATTERS.** Existing **unverified** password account (the squatter):
   register it, keep the squatter's token. Google sign-in for that email. Then assert:
   - the squatter's old password → login **401**
   - the squatter's old token → `/auth/me` **401**
   - the row is now verified and has `google_sub`, same id
   - the new Google token → `/auth/me` **200**
8. `email_verified: false` from Google → `oauth_error=unverified`, **no account created or changed**.
9. `validate_id_token` rejects: wrong `aud`, wrong `iss`, expired `exp`, wrong `nonce`,
   missing `email_verified` — five separate asserts, each paired with the valid token passing.
10. `ADMIN_EMAILS` contains the Google email → user is admin (Google verified the address).
11. After any callback, the `g_oauth` cookie is cleared.

**Then prove #7 discriminates** — the standing rule, an assertion nobody has watched fail
is not evidence: temporarily make the unverified case behave like the verified case (link
instead of take over), run the suite, **paste the failure**, revert.

### `GoogleComplete.test.tsx`

- a hash with a token → `setSession` called, **the hash is removed from the URL**, navigates to `/app`
- no token in the hash → navigates to `/login?oauth_error=failed`
- `me()` rejects → `/login?oauth_error=failed`, no token left stored

### The full gate

```bash
cd stratoscan && export JWT_SECRET=ci-dummy-jwt-secret
python -m tests.test_google_oauth
for t in tests/test_*.py; do python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"; done
ruff check .
python -c "from src.config import validate_env_template as v; print(v() or 'complete')"
git diff --exit-code requirements.txt requirements-lock.txt && echo "deps unchanged"
git grep -nE 'GOCSPX-|apps\.googleusercontent\.com"' -- . ':!*.md' && echo "SECRET-LOOKING VALUE FOUND — STOP" || echo "no secrets committed"

cd ../frontend
npm run typecheck && npm run lint && npm run format:check && npx vitest run && npm run build
```

### The manual check (the owner does this — you cannot, there is no real key in your copy)

Write these steps at the end of your report so the owner can follow them:

1. `make rebuild`
2. open `http://localhost:5173/login`, click **Continue with Google**, choose a test-user account
3. expect to land on the dashboard, signed in
4. sign out, sign in with Google again → same account, not a new one

## Done

Commit and push branch `task/009-google-oauth`. **Do not merge.** Claude reviews it first —
particularly the merge rule, the state cookie, `validate_id_token`, and that no secret is
anywhere in the diff.
