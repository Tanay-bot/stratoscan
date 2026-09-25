# 008 — `token_version`: make a session revocable

## Goal

A JWT issued by this app is valid until it expires, full stop. Nothing can end a session
early — not a password change, not a stolen laptop, not the user asking.

Add a `token_version` integer to `users`, stamp it into every token, and check it on every
authenticated request. Bumping the column invalidates every token that account ever
issued, instantly. Then expose that as `POST /auth/logout-all`.

This is the primitive four rows of Appendix A depend on, and Day 3's password reset is
unsafe without it — a reset that leaves the attacker's session live looks like security
and is not. See `DAY-03-PLAN.md` for why this runs first.

It is cheap here, which is the reason not to reach for a token blocklist or a session
store: `get_current_user` already calls `_fetch_user` on **every** authenticated request
(`auth.py:239`), so the row is in hand. This adds one integer comparison — no extra query,
no cache, no dependency.

## Files

- `stratoscan/src/database.py` *(schema + idempotent backfill + one method)*
- `stratoscan/alembic/versions/<new>.py` *(the 12th revision)*
- `stratoscan/src/auth.py` *(stamp the claim, check the claim)*
- `stratoscan/src/api.py` *(one new route; token issuance at register + login)*
- `stratoscan/tests/fakes.py` *(see Trap 1 — this is not optional)*
- `stratoscan/tests/test_token_revocation.py` *(new)*
- `stratoscan/openapi.json`, `stratoscan/openapi.pretty.json` *(regenerated — see Trap 2)*

Nothing else. **Do not touch `RATE_LIMITS`, `_scope_for_path`, or any rate-limit
constant** — `/auth/logout-all` already lands in the `auth` scope by prefix match
(`api.py:183`), which is correct. Golden rule #11.

## Pattern

### The column

In `database.py`, beside the existing `users` DDL (`database.py:446`):

```sql
token_version       INTEGER NOT NULL DEFAULT 0
```

`CREATE TABLE IF NOT EXISTS` never alters an existing table, so it also needs an
idempotent backfill in the style of `USERS_EMAIL_VERIFICATION_BACKFILL`
(`database.py:464`):

```python
USERS_TOKEN_VERSION_BACKFILL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
]
```

Wire it into `init_schema()` exactly the way the verification backfill is wired. That
self-heal is what makes a bare deploy work (CLAUDE.md §18), and the Alembic revision is
what makes a managed one work. Both, not either.

Add one method, next to `set_user_admin`:

```python
def bump_token_version(self, user_id: int) -> int:
    """Invalidate every token this user currently holds. Returns the new version."""
```

Use `UPDATE users SET token_version = token_version + 1 WHERE id = %s RETURNING
token_version` — one statement, no read-modify-write, so two concurrent "log out
everywhere" calls cannot lose one another's bump.

### The claim

`create_access_token(user_id, email)` gains a third parameter and stamps `ver`:

```python
def create_access_token(user_id: int, email: str, token_version: int = 0) -> str:
```

`verify_access_token` returns the payload unchanged — it must NOT reach the database;
keep it a pure signature/expiry check, which is what makes it testable and fast.

The comparison belongs in `get_current_user`, after `_fetch_user` returns:

```python
if payload.get("ver") != user.get("token_version", 0):
    raise HTTPException(status_code=401, detail="Session expired. Log in again.")
```

**Decide and state the missing-claim rule, because the default is a silent bypass.** A
token minted before this change has no `ver`, so `payload.get("ver")` is `None`, which
never equals `0` — it is rejected. **That is the intended behaviour: keep it.** Every
existing session is logged out once, which affects a handful of local dev accounts and
nothing else. Do not add `payload.get("ver", 0)` to "be nice to existing tokens" — that
makes a pre-change token permanently unrevocable, which is precisely the hole this task
exists to close.

Both issuance sites must pass the real version: `/auth/register` (a fresh user is `0`) and
`/auth/login` (read it off the user row). Search for `create_access_token(` and fix every
call site; a missed one issues a token that 401s on its first authenticated request.

### The route

```
POST /auth/logout-all      Bearer, no body
→ {"error": false, "token_version": <new>, "message": "..."}
```

Bump, then return. The caller's own token is now invalid too — that is correct and worth
saying in the docstring, because it is the first thing a reviewer will flag as a bug.

### The test

`tests/test_token_revocation.py`, this repo's script style (module-level asserts,
`print("✅ …")` per check, `Run: python -m tests.test_token_revocation` in the docstring —
see `tests/test_emails_smtp.py`). Hermetic: `FakeUserDB`, no network, no real DB.

Pin at minimum:

- A token issued, then `bump_token_version`, then the same token on an authed endpoint →
  **401**, and the body leaks nothing about why.
- A token issued *after* the bump still works. (Without this, "everything 401s" passes.)
- **Two devices**: two tokens for one account, bump once, **both** stop working.
- A token with **no `ver` claim at all** → 401. Construct it by calling the token builder
  the pre-change way, or by signing a payload without the claim — assert the rule above
  rather than assuming it.
- Two bumps in a row → version 2, and the version-1 token is still dead.
- `POST /auth/logout-all` without a Bearer → 401, not 500.
- A bump on user A does **not** invalidate user B's token.

That second bullet and the last are the discriminating ones. A revocation check wired to
reject unconditionally passes every other assertion on this list.

## ⚠️ Three traps

**1. `FakeUserDB` will break half the suite, and the failure will look unrelated.**
`tests/fakes.py:73` `_store()` builds the user dict literally; it has no `token_version`
key, so `user.get("token_version", 0)` returns the default while real tokens carry the
real value — or worse, the auth suites start failing with 401s that look like a bug in
your new code. Add `"token_version": 0` to `_store`, and a `bump_token_version` method
matching `PostgresDB`'s exact signature and return. `FakeUserDB` is documented as having
"the exact method surface" (CLAUDE.md §14); keep that true.

**2. The OpenAPI snapshots are a contract and a test.** A new route changes the schema,
and `tests/test_docs_contract.py` pins the committed `openapi.json` /
`openapi.pretty.json` against the live app. Regenerate both with `python
scripts/regen_openapi.py` and commit them, or that suite fails and it is not obvious why.
Golden rule #10.

**3. `/meta` is pinned too.** `tests/test_frontend_meta.py` pins the capability manifest,
whose endpoint catalog is derived from the live app (`api.py:1425`). If adding a route
changes that manifest, the test tells you; update the manifest, not the test's
expectations, unless you can say why the new expectation is right.

## Non-goals

- **No password reset, refresh tokens, email change, deletion, or OAuth.** Those are specs
  009–012 and each gets its own review. This task is the primitive alone.
- Do not bump the version anywhere except `/auth/logout-all`. Password-change and reset
  bumps arrive with 009, where they can be tested against a real reset flow.
- Do not change `verify_access_token` into something that reads the database.
- Do not touch `jwt_expire_hours`, the PBKDF2 parameters, the dummy-hash timing
  equalisation, or `require_admin`.
- Do not add a rate-limit scope, bucket, or cap (golden rule #11).
- Do not modify the frontend. The `401 → auto-logout` interceptor
  (`lib/api/client.ts`) already handles this correctly; wiring a UI button is a later task.
- No reformatting, renaming or drive-by cleanup.

## Baseline

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

Prints nothing = **41/41**. You add one suite, so the target is **42/42**.

## Verify

```bash
cd stratoscan && export JWT_SECRET=ci-dummy-jwt-secret

python -m tests.test_token_revocation     # exit 0
python -m tests.test_auth                 # exit 0 — the existing auth suite
python -m tests.test_email_verification   # exit 0 — issues tokens too
python -m tests.test_admin                # exit 0 — FakeUserDB surface
python -m tests.test_docs_contract        # exit 0 — snapshots regenerated
python -m tests.test_frontend_meta        # exit 0
python -m tests.test_migrations           # exit 0
python -m tests.test_migration_behavior   # exit 0

ruff check .
python -c "from src.config import validate_env_template as v; print(v() or 'complete')"

for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done                                       # prints nothing = 42/42

git diff --exit-code requirements.txt requirements-lock.txt && echo "deps unchanged"
```

Then against the live stack, because the migration path is the half unit tests cannot
reach:

```bash
make rebuild        # exit 0 — the entrypoint runs `alembic upgrade head` for real
make verify         # exit 0 — still green
```

Then prove revocation end to end on the running API, register → authed call → logout-all →
same token:

```bash
# register, keep the token; call an authed endpoint (expect 200);
# POST /auth/logout-all; call the SAME authed endpoint again (expect 401)
```

Write it properly. The 200 before the bump is the part that makes the 401 after it mean
something — report both, not just the 401.

**Prove the check discriminates**, as with the last two tasks. Observe these failing:

- delete the `ver` comparison in `get_current_user` → the two-device test must **fail**;
- leave the comparison but revert `_store` to omit `token_version` → report what happens
  (this is trap 1; the point is to see its signature, not to guess it).

## Done

Commit and push branch `task/008-token-version`. **Do not merge.** Claude reviews the diff
first — particularly the missing-claim rule, the `FakeUserDB` surface, and every
`create_access_token` call site.
