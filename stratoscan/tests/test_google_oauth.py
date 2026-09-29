"""Hermetic tests for "Continue with Google" — no DB, no network, no real keys.

The flow under test is the server-side authorization-code flow
(GET /auth/google/start → Google → GET /auth/google/callback), with the one
network call (``src.google_oauth.exchange_code``) patched, so this suite never
opens a socket. The ``id_token`` is a hand-built JWS whose signature is never
verified by design — the token only ever arrives straight from Google's token
endpoint over TLS (OpenID Connect Core §3.1.3.7), which is exactly what the
patched ``exchange_code`` stands in for.

Section 7 is the reason this file exists. A Google email matching an UNVERIFIED
password account is the classic account-takeover bug in this feature: linking
instead of taking over leaves a stranger's password able to open the real
owner's account, with the stranger's existing session still alive. The other
sections pin everything the merge rule depends on (verified state from Google,
nonce, state, aud, iss, exp) so a "fix" that weakens one of them fails loudly.

Run: python -m tests.test_google_oauth
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic env, pinned BEFORE the app import:
#   - rate limits off (this suite makes far more /auth calls than the 10/min
#     auth bucket allows; test_rate_limits pins the bucket numbers themselves)
#   - a JWT secret (auth endpoints 503 without one, and it signs the state cookie)
#   - development (so the state cookie is not marked Secure and can round-trip
#     over the test client's http://testserver)
#   - NO mail transport, so registration must never attempt SendGrid or SMTP
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "google-oauth-dummy-secret")
os.environ["APP_ENV"] = "development"
os.environ["SENDGRID_API_KEY"] = ""
os.environ["SMTP_HOST"] = ""
os.environ["EMAIL_FROM_ADDRESS"] = ""
os.environ["EMAIL_VERIFICATION_REQUIRED"] = "false"

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.config import settings  # noqa: E402
from src.google_oauth import (  # noqa: E402
    COOKIE_NAME,
    COOKIE_PATH,
    IdTokenError,
    validate_id_token,
    verify_state_cookie,
)
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

# ── Hermetic setup: fake DB + fake LLM ──────────────────────────────────────
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app  # noqa: E402  (env must be pinned before the import)

# follow_redirects=False everywhere: the 302s ARE the assertions here, and
# following the /start redirect would try to reach accounts.google.com.
client = TestClient(app)

# Fake credentials. The client id deliberately does NOT look like a real one
# (see .env.example: empty there, real values only in a git-ignored .env).
GOOGLE_CLIENT_ID = "test-client-id"
GOOGLE_CLIENT_SECRET = "test-client-secret"
APP_BASE_URL = "http://localhost:5173"
PASSWORD = "hunter2hunter2"

CONFIG = {
    "google_client_id": GOOGLE_CLIENT_ID,
    "google_client_secret": GOOGLE_CLIENT_SECRET,
    "app_base_url": APP_BASE_URL,
}
UNCONFIGURED = {
    "google_client_id": "",
    "google_client_secret": "",
    "app_base_url": APP_BASE_URL,
}


def _configured():
    """Patch the three settings the flow reads (restored on exit)."""
    return mock.patch.multiple(settings, **CONFIG)


def _unconfigured():
    return mock.patch.multiple(settings, **UNCONFIGURED)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _id_token(claims: dict) -> str:
    """Build a JWS-shaped id_token with arbitrary claims.

    The signature segment is junk on purpose: nothing in the flow verifies it,
    because the token is only ever read from Google's token-endpoint response
    (see the module docstring). A test that "passes" because the signature was
    ignored is testing the right thing.
    """
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}.{_b64(b'never-checked')}"


def _claims(sub: str, email: str, nonce: str, **overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": GOOGLE_CLIENT_ID,
        "sub": sub,
        "email": email,
        "email_verified": True,
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return claims


def _start():
    """GET /auth/google/start without following the redirect."""
    return client.get("/auth/google/start", follow_redirects=False)


def _callback(code="auth-code-1", state="", cookie=None, error=""):
    """GET /auth/google/callback with an EXPLICIT cookie (never the jar)."""
    client.cookies.clear()
    params = {"state": state}
    if code:
        params["code"] = code
    if error:
        params["error"] = error
    headers = {"Cookie": f"{COOKIE_NAME}={cookie}"} if cookie else {}
    return client.get(
        "/auth/google/callback", params=params, headers=headers, follow_redirects=False
    )


def _sign_in(sub: str, email: str, **claim_overrides):
    """Run start → callback end to end with a patched code exchange."""
    start = _start()
    assert start.status_code == 302, f"start: {start.status_code}"
    cookie = start.cookies.get(COOKIE_NAME)
    claims = verify_state_cookie(cookie)
    assert claims, "the start response must set a verifiable state cookie"
    token_claims = _claims(sub, email, claims["nonce"], **claim_overrides)
    with mock.patch(
        "src.google_oauth.exchange_code",
        return_value={"id_token": _id_token(token_claims)},
    ):
        return _callback(code="auth-code-1", state=claims["state"], cookie=cookie)


def _location(resp) -> str:
    return resp.headers.get("location", "")


def _error_code(resp) -> str:
    """The oauth_error code from a failure redirect."""
    location = _location(resp)
    assert location.startswith(f"{APP_BASE_URL}/login?"), location
    return urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["oauth_error"][0]


def _jwt_from(resp) -> str:
    """The JWT carried by a success redirect's FRAGMENT (never the query)."""
    location = _location(resp)
    fragment = urllib.parse.urlsplit(location).fragment
    assert fragment, f"no fragment in {location}"
    return urllib.parse.parse_qs(fragment)["token"][0]


def _me(token: str):
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


def _sub_of(token: str) -> int:
    from src.auth import verify_access_token

    payload = verify_access_token(token)
    assert payload, "the minted token must verify"
    return int(payload["sub"])


def _location_has_clear_cookie(resp) -> bool:
    """True when the response clears the state cookie (Max-Age=0)."""
    for header in resp.headers.get_list("set-cookie"):
        lowered = header.lower()
        if lowered.startswith(f"{COOKIE_NAME}=") and "max-age=0" in lowered:
            assert COOKIE_PATH in header, header
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 1. /auth/google/start — the consent-screen redirect + state cookie
# ═══════════════════════════════════════════════════════════════════════════
print("1. /auth/google/start → 302 to Google with a signed state cookie...")
with _configured():
    start = _start()
assert start.status_code == 302, f"start: {start.status_code} {start.text[:200]}"
location = _location(start)
assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?"), location
params = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
assert params["client_id"] == [GOOGLE_CLIENT_ID], params
# The EXACT redirect URI: Google compares it character for character against
# the console entry, and the same value is replayed at the token endpoint.
assert params["redirect_uri"] == ["http://localhost:8000/auth/google/callback"], params
assert params["response_type"] == ["code"], params
assert params["scope"] == ["openid email profile"], params
assert params["prompt"] == ["select_account"], params
assert params["state"][0] and params["nonce"][0], params

set_cookie = start.headers["set-cookie"]
lowered = set_cookie.lower()
assert set_cookie.startswith(f"{COOKIE_NAME}="), set_cookie
assert "httponly" in lowered, set_cookie
# Path=/auth/google — the cookie is not attached to any other request.
assert f"path={COOKIE_PATH}".lower() in lowered, set_cookie
assert "samesite=lax" in lowered, set_cookie
assert "max-age=600" in lowered, set_cookie
# Secure only in production: the local stack is plain http, and a Secure
# cookie would simply never come back, breaking the round trip in dev.
assert "secure" not in lowered, set_cookie
cookie_value = start.cookies.get(COOKIE_NAME)
claims = verify_state_cookie(cookie_value)
assert claims, "the state cookie must verify against jwt_secret"
assert claims["state"] == params["state"][0], "cookie state must match the URL state"
assert claims["nonce"] == params["nonce"][0], "cookie nonce must match the URL nonce"
print("✅ 302 to accounts.google.com; client_id/redirect_uri/state/nonce/scope exact; HttpOnly + Path=/auth/google cookie")

# A tampered cookie is indistinguishable from no cookie at all — both fail
# closed (asserted in section 3, which is where the CSRF rule lives).
assert verify_state_cookie(cookie_value[:-1] + ("A" if cookie_value[-1] != "A" else "B")) is None
assert verify_state_cookie(None) is None
print("✅ a tampered or missing state cookie does not verify")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Not configured → the login page, not a broken consent screen
# ═══════════════════════════════════════════════════════════════════════════
print("2. Unconfigured → /login?oauth_error=unavailable...")
with _unconfigured():
    start = _start()
    assert start.status_code == 302, start.status_code
    assert _location(start) == f"{APP_BASE_URL}/login?oauth_error=unavailable", _location(start)
    # The callback must refuse too: without the pair there is nothing to
    # exchange a code against, and it must not 500.
    cb = _callback(code="x", state="y", cookie="anything")
    assert cb.status_code == 302, cb.status_code
    assert _error_code(cb) == "unavailable", _location(cb)
print("✅ both routes degrade to oauth_error=unavailable")


# ═══════════════════════════════════════════════════════════════════════════
# 3. The CSRF rule: no cookie, tampered cookie, or wrong state → "state"
# ═══════════════════════════════════════════════════════════════════════════
print("3. Missing / tampered / mismatched state → oauth_error=state, no user created...")
with _configured():
    users_before = len(fake_db._users)

    cb = _callback(code="auth-code-1", state="whatever", cookie=None)
    assert _error_code(cb) == "state", _location(cb)

    real_cookie = _start().cookies.get(COOKIE_NAME)
    tampered = real_cookie[:-1] + ("A" if real_cookie[-1] != "A" else "B")
    cb = _callback(code="auth-code-1", state=verify_state_cookie(real_cookie)["state"], cookie=tampered)
    assert _error_code(cb) == "state", _location(cb)

    # A perfectly valid cookie whose state does NOT match the callback's: the
    # classic login-CSRF shape (attacker's code, victim's browser).
    cb = _callback(code="auth-code-1", state="not-the-cookie-state", cookie=real_cookie)
    assert _error_code(cb) == "state", _location(cb)

    assert len(fake_db._users) == users_before, "no account may be created on a state failure"
print("✅ all three → oauth_error=state, nothing created")


# ═══════════════════════════════════════════════════════════════════════════
# 4. A first-time Google user: created, verified, token in the FRAGMENT
# ═══════════════════════════════════════════════════════════════════════════
print("4. New Google user → account created; token in the fragment...")
with _configured():
    resp = _sign_in("google-sub-new", "new-google-user@example.com")
    assert resp.status_code == 302, f"{resp.status_code} {resp.text[:200]}"
    location = _location(resp)
    assert location.startswith(f"{APP_BASE_URL}/auth/google/complete#"), location
    assert "#token=" in location, location
    # Fragments are not sent to servers or written to access logs; a query
    # string token would leak into both. This is the whole reason for "#".
    assert "?token=" not in location, location

    row = fake_db.get_user_by_email("new-google-user@example.com")
    assert row, "the account must exist"
    assert row["email_verified"] is True, "Google proved the address"
    assert row["google_sub"] == "google-sub-new", row
    assert row["token_version"] == 0, row
    # An UNUSABLE password, but a REAL hash: verify_password rejects a malformed
    # hash in microseconds, which would leak which accounts are Google-only.
    assert str(row["password_hash"]).startswith("pbkdf2_sha256$"), row["password_hash"]
    from src.auth import verify_password

    assert not verify_password("", row["password_hash"])
    assert not verify_password(PASSWORD, row["password_hash"])

    token = _jwt_from(resp)
me = _me(token)
assert me.status_code == 200, f"the minted token must work: {me.status_code} {me.text[:200]}"
assert me.json()["email"] == "new-google-user@example.com"
assert _sub_of(token) == row["id"]
print("✅ created + verified + google_sub stored; #token= (not ?token=) → /auth/me 200")


# ═══════════════════════════════════════════════════════════════════════════
# 5. A returning user is found by `sub`, even after a Google email change
# ═══════════════════════════════════════════════════════════════════════════
print("5. Returning by sub, Google email changed → the SAME user...")
with _configured():
    first_id = _sub_of(_jwt_from(_sign_in("google-sub-return", "before@example.com")))
    # Google changed the address: `sub` is stable forever, the email is not.
    resp = _sign_in("google-sub-return", "after@example.com")
    assert resp.status_code == 302, resp.status_code
    second_id = _sub_of(_jwt_from(resp))
    assert first_id == second_id, f"sub lookup must win: {first_id} != {second_id}"
    assert _me(_jwt_from(resp)).status_code == 200
    # No second row was created for the new address.
    assert fake_db.get_user_by_email("after@example.com") is None
print(f"✅ same user id ({first_id}) after the email changed; no duplicate account")


# ═══════════════════════════════════════════════════════════════════════════
# 6. A VERIFIED password account → linked, password keeps working
# ═══════════════════════════════════════════════════════════════════════════
print("6. Existing verified account → linked; its password still logs in...")
with _configured():
    email = "verified-password-user@example.com"
    reg = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert reg.status_code == 200, reg.text[:200]
    registered_id = _sub_of(reg.json()["access_token"])
    row = fake_db.get_user_by_email(email)
    assert row["email_verified"] is True, "no mail transport configured → starts verified"

    resp = _sign_in("google-sub-link", email)
    assert resp.status_code == 302, resp.status_code
    linked_token = _jwt_from(resp)
    assert _sub_of(linked_token) == registered_id, "linking must reuse the row"
    row = fake_db.get_user_by_email(email)
    assert row["google_sub"] == "google-sub-link", row
    assert row["token_version"] == 0, "linking is not a takeover — no bump"
    # The password survives, because the address was already proven.
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, f"the password must keep working: {login.text[:200]}"
    assert _me(linked_token).status_code == 200
print("✅ linked to the same id, google_sub set, no version bump, password unchanged")


# ═══════════════════════════════════════════════════════════════════════════
# 7. THE ONE THAT MATTERS — the unverified "squatter" account
# ═══════════════════════════════════════════════════════════════════════════
print("7. Unverified account with the Google address → taken over...")
with _configured():
    email = "squatted-address@example.com"
    # Register it FOR REAL, with verification configured and the mail send
    # mocked, so the row is exactly what a stranger could create: a password of
    # their choosing on an address whose inbox nobody ever proved.
    with mock.patch.multiple(
        settings,
        sendgrid_api_key="SG.not-a-real-key",
        email_from_address="noreply@example.test",
    ), mock.patch("src.emails.send_verification_email", return_value=True):
        reg = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert reg.status_code == 200, reg.text[:200]
    squatter_token = reg.json()["access_token"]
    squatter_id = _sub_of(squatter_token)
    row = fake_db.get_user_by_email(email)
    assert row["email_verified"] is False, "the squatter's inbox was never proved"
    assert _me(squatter_token).status_code == 200, "the squatter's session is live first"

    resp = _sign_in("google-sub-owner", email)
    assert resp.status_code == 302, f"{resp.status_code} {resp.text[:200]}"
    owner_token = _jwt_from(resp)

    # 1. The stranger's password no longer opens the account.
    assert row["password_hash"] != "" and row["email_verified"] is True, row
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 401, f"the squatter's password must die: {login.status_code}"
    # 2. The session the stranger was already holding is dead (token_version+1).
    assert row["token_version"] == 1, row
    stale = _me(squatter_token)
    assert stale.status_code == 401, f"the squatter's token must die: {stale.status_code}"
    # 3. Same row, now owned by the Google identity, and verified.
    assert _sub_of(owner_token) == squatter_id, "the takeover must reuse the row"
    assert row["google_sub"] == "google-sub-owner", row
    assert row["verification_token"] is None, "pending tokens are cleared"
    assert row["reset_token_hash"] is None, "pending tokens are cleared"
    # 4. The real owner is signed in.
    assert _me(owner_token).status_code == 200, "the Google token must work"
print("✅ squatter: password 401, old token 401, row verified + google_sub, owner token 200")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Google says the address is unverified → refuse, touch nothing
# ═══════════════════════════════════════════════════════════════════════════
print("8. email_verified=false from Google → oauth_error=unverified, nothing changes...")
with _configured():
    fresh_email = "unverified-google-address@example.com"
    resp = _sign_in("google-sub-unverified", fresh_email, email_verified=False)
    assert resp.status_code == 302, resp.status_code
    assert _error_code(resp) == "unverified", _location(resp)
    assert fake_db.get_user_by_email(fresh_email) is None, "no account may be created"

    # And an existing verified account must not be linked either.
    existing = "verified-password-user@example.com"
    before = dict(fake_db.get_user_by_email(existing))
    resp = _sign_in("google-sub-unverified-2", existing, email_verified=False)
    assert _error_code(resp) == "unverified", _location(resp)
    after = dict(fake_db.get_user_by_email(existing))
    assert after == before, "an unverified Google address must change nothing"
print("✅ unverified address → oauth_error=unverified; no account created or changed")


# ═══════════════════════════════════════════════════════════════════════════
# 9. validate_id_token — five rejections, each next to the valid token passing
# ═══════════════════════════════════════════════════════════════════════════
print("9. validate_id_token rejects bad aud / iss / exp / nonce / missing email_verified...")
with _configured():
    nonce = "the-nonce-for-this-flow"
    good = _claims("google-sub-claims", "claims@example.com", nonce)
    # The acceptance half: the exact token that every rejection below mutates.
    validated = validate_id_token(_id_token(good), nonce)
    assert validated["sub"] == "google-sub-claims", validated

    def _rejects(**overrides) -> IdTokenError:
        """Assert the mutated token is rejected, and return the reason."""
        try:
            validate_id_token(_id_token({**good, **overrides}), nonce)
        except IdTokenError as e:
            return e
        raise AssertionError(f"expected a rejection for {overrides}")

    err = _rejects(aud="some-other-client")
    assert err.code == "token" and "audience" in str(err), err
    err = _rejects(iss="https://evil.example.com")
    assert err.code == "token" and "issuer" in str(err), err
    err = _rejects(exp=int(time.time()) - 10)
    assert err.code == "token" and "expired" in str(err), err
    err = _rejects(nonce="a-different-nonce")
    assert err.code == "token" and "nonce" in str(err), err
    err = _rejects(email_verified=False)
    assert err.code == "unverified", err
    missing = {k: v for k, v in good.items() if k != "email_verified"}
    try:
        validate_id_token(_id_token(missing), nonce)
        raise AssertionError("a missing email_verified claim must be rejected")
    except IdTokenError as e:
        assert e.code == "unverified", e
    # Non-boolean truthy values are not proof either.
    err = _rejects(email_verified="true")
    assert err.code == "unverified", err
    # And the valid token still passes after all five mutations.
    assert validate_id_token(_id_token(good), nonce)["email"] == "claims@example.com"
print("✅ wrong aud/iss/exp/nonce/email_verified all rejected; the valid token still passes")


# ═══════════════════════════════════════════════════════════════════════════
# 10. ADMIN_EMAILS applies to a Google sign-in (Google verified the address)
# ═══════════════════════════════════════════════════════════════════════════
print("10. A Google sign-in for an ADMIN_EMAILS address is promoted...")
with _configured(), mock.patch.multiple(settings, admin_emails="boss@example.com"):
    resp = _sign_in("google-sub-admin", "boss@example.com")
    assert resp.status_code == 302, resp.status_code
    token = _jwt_from(resp)
    me = _me(token)
    assert me.status_code == 200, me.text[:200]
    assert me.json()["is_admin"] is True, me.json()
print("✅ is_admin=True on the Google session (the address was Google-verified)")


# ═══════════════════════════════════════════════════════════════════════════
# 11. The state cookie is cleared on EVERY outcome
# ═══════════════════════════════════════════════════════════════════════════
print("11. The g_oauth cookie is cleared on success and on failure...")
with _configured():
    success = _sign_in("google-sub-cookie", "cookie-check@example.com")
    assert _location_has_clear_cookie(success), success.headers.get_list("set-cookie")

    real_cookie = _start().cookies.get(COOKIE_NAME)
    failure = _callback(code="auth-code-1", state="wrong", cookie=real_cookie)
    assert _error_code(failure) == "state"
    assert _location_has_clear_cookie(failure), failure.headers.get_list("set-cookie")

    unconfigured = _callback(code="x", state="y", cookie="anything")
    assert _location_has_clear_cookie(unconfigured), unconfigured.headers.get_list("set-cookie")
print("✅ cleared on success, on a state failure, and on an unavailable flow")

print()
print("🎯 All Google OAuth tests pass!")
