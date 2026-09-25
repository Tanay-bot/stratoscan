"""Hermetic tests for signup email verification (src/emails.py + auth API).

Run: python -m tests.test_email_verification

Fully hermetic — zero network, zero real SendGrid, zero Neon:

1. Token generation: URL-safe, random, right length.
2. Verification URL builder: uses APP_BASE_URL, strips trailing slashes.
3. send_email / send_verification_email: the SendGrid REST call is mocked
   at urllib.request.urlopen — verifies the bearer auth header, JSON
   payload shape (personalizations/to, from, subject, content), the
   verification-link text, and that failures (HTTPError, unconfigured
   key) return False instead of raising.
4. DB methods via FakeUserDB: create_user stores email_verified + token;
   get_user_by_verification_token finds the row; mark_email_verified flips
   the flag and consumes the token.
5. API flow through the REAL FastAPI app (fake DB + fake LLM + fake email):
   - register with verification configured → email_sent=True, user
     email_verified=False, token stored
   - GET /auth/verify-email?token=... → verified=True, DB flipped
   - expired token → 400; unknown/already-used token → 400
   - POST /auth/resend-verification (authenticated) → fresh token + email
   - EMAIL_VERIFICATION_REQUIRED=true blocks unverified login (403) and
     allows it after verification
   - register with verification DISABLED → email_verified=True immediately
     (no dangling unverified state when SendGrid isn't wired)
"""

from __future__ import annotations

import sys
import os
import json
import io
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
# Force email verification ON for this suite (like a production .env), so
# the register flow generates + stores tokens. Individual tests override
# back to off to prove the disabled path.
os.environ["SENDGRID_API_KEY"] = "SG.testkey123"
os.environ["EMAIL_FROM_ADDRESS"] = "noreply@stratoscan.app"
os.environ["APP_BASE_URL"] = "https://app.stratoscan.com"

from unittest import mock  # noqa: E402

from src.config import settings  # noqa: E402
from src.emails import (  # noqa: E402
    SENDGRID_MAIL_URL,
    build_verification_url,
    generate_verification_token,
    send_email,
    send_verification_email,
)
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app  # noqa: E402

client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)

# A deadline always in the future relative to run time — hand-seeded users
# must be verifiable regardless of when the suite runs. (A hardcoded date
# would silently expire once the calendar passes it.)
_FUTURE = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


# ── 1. Token generation ───────────────────────────────────────────────────────
print("1. Token generation...")
t1 = generate_verification_token()
t2 = generate_verification_token()
assert len(t1) >= 40 and t1 == t1.strip(), "token too short / has whitespace"
assert t1 != t2, "tokens must be random"
assert all(c.isalnum() or c in "-_" for c in t1), "token not URL-safe"
print("✅ tokens are random, URL-safe, and long enough")


# ── 2. Verification URL builder ──────────────────────────────────────────────
print("2. Verification URL builder...")
url = build_verification_url("abc123")
assert url == "https://app.stratoscan.com/verify-email?token=abc123", url
print("✅ URL uses APP_BASE_URL + token query param")


# ── 3. SendGrid client (urllib mocked — no network) ──────────────────────────
print("3. SendGrid client (mocked urlopen)...")
_captured: dict = {}


class _FakeResponse:
    """Stand-in for ``urllib.response.addinfourl`` — must support the
    context-manager protocol because ``send_email`` uses ``with
    urlopen(...) as resp:`` (missing __enter__/__exit__ is exactly the
    error this fake originally triggered)."""

    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(request, timeout=10):
    _captured["url"] = request.full_url
    _captured["method"] = request.get_method()
    _captured["auth"] = request.get_header("Authorization")
    _captured["body"] = json.loads(request.data.decode("utf-8"))
    return _FakeResponse()


# 3a. Success → True + correct wire format.
with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
    ok = send_verification_email("user@example.com", "tok123")
assert ok is True, "202 must be treated as success"
assert _captured["url"] == SENDGRID_MAIL_URL
assert _captured["method"] == "POST"
assert _captured["auth"] == "Bearer SG.testkey123"
body = _captured["body"]
assert body["personalizations"][0]["to"] == [{"email": "user@example.com"}]
assert body["from"] == {"email": "noreply@stratoscan.app"}
assert "Verify your Stratoscan account" == body["subject"]
text = body["content"][0]["value"]
assert "verify-email?token=tok123" in text and "expires" in text
print("✅ send_verification_email → 202, correct payload + link")

# 3b. HTTPError (e.g. 403 unverified sender) → False, no raise. A REAL
# urllib.error.HTTPError so the ``except HTTPError`` branch is exercised
# (a generic Exception would miss it and fall through to the catch-all).
def _raise_http_error(request, timeout=10):
    raise HTTPError(
        SENDGRID_MAIL_URL, 403, "Forbidden", {},
        io.BytesIO(b'{"errors":[{"message":"sender not verified"}]}'),
    )


with mock.patch("urllib.request.urlopen", side_effect=_raise_http_error):
    assert send_email("x@y.z", "s", "b") is False
print("✅ HTTP 403 → False (sender not verified case)")

# 3c. Unconfigured key → False, no network attempt.
with mock.patch("src.emails.settings") as ms:
    ms.sendgrid_api_key = ""
    ms.smtp_host = ""          # keep this case on the SendGrid path
    ms.email_from_address = "noreply@stratoscan.app"
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network!")):
        assert send_email("x@y.z", "s", "b") is False
print("✅ unconfigured key → False without any network call")


# ── 4. DB methods via FakeUserDB ─────────────────────────────────────────────
print("4. DB methods (FakeUserDB)...")
uid = fake_db.create_user(
    "bob@example.com", "hash", plan="free",
    email_verified=False,
    verification_token="tokbob",
    verification_token_expires=_FUTURE,
)
row = fake_db.get_user_by_verification_token("tokbob")
assert row and row["id"] == uid and row["email"] == "bob@example.com", row
assert fake_db.get_user_by_verification_token("nope") is None
fake_db.mark_email_verified(uid)
bob = fake_db.get_user_by_id(uid)
assert bob["email_verified"] is True
# The token is KEPT (it can only mark its own account verified) so that a
# second click — or a mail scanner's pre-fetch — is recognised as "already
# verified" instead of "invalid link". Appendix A: clicked twice → idempotent.
again = fake_db.get_user_by_verification_token("tokbob")
assert again and again["email_verified"] is True, again
print("✅ store/lookup/verify lifecycle correct; token kept for idempotent re-clicks")


# ── 5. API flow (real app, fakes for DB/LLM/email) ───────────────────────────
print("5. API flow...")

# 5a. Register with verification configured → email_sent=True, not verified.
with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
    r = client.post(
        "/auth/register",
        json={"email": "carol@stratoscan.app", "password": "hunter2hunter2"},
    )
assert r.status_code == 200, r.text
reg = r.json()
assert reg["email_sent"] is True, "verification email must be sent"
assert reg["user"]["email_verified"] is False, "new users start unverified"
carol = fake_db.get_user_by_email("carol@stratoscan.app")
assert carol["verification_token"], "token must be stored on the user row"
# Capture the token for the re-click assertions below.
carol_token = carol["verification_token"]
print("✅ register → email sent, user unverified, token stored")

# 5b. Verify via the emailed token (GET form).
r = client.get(f"/auth/verify-email?token={carol_token}")
assert r.status_code == 200, r.text
assert r.json()["verified"] is True
assert fake_db.get_user_by_email("carol@stratoscan.app")["email_verified"] is True
print("✅ GET /auth/verify-email flips the flag")

# 5c. Clicked twice → idempotent success, not an error page (Appendix A).
r = client.get(f"/auth/verify-email?token={carol_token}")
assert r.status_code == 200 and r.json()["verified"] is True, f"second click must succeed: {r.status_code} {r.text}"
# ...even once the link itself has expired: the account IS verified.
fake_db._users["carol@stratoscan.app"]["verification_token_expires"] = "2000-01-01T00:00:00+00:00"
r = client.get(f"/auth/verify-email?token={carol_token}")
assert r.status_code == 200 and r.json()["verified"] is True, r.text
# The discriminating half: a token that was never issued still fails.
r = client.get("/auth/verify-email?token=never-issued-token-xyz")
assert r.status_code == 400, r.status_code
print("✅ second click (even after expiry) → 200 verified; a never-issued token → 400")

# 5d. POST form + unknown token.
uid2 = fake_db.create_user(
    "dave@example.com", "hash", plan="free", email_verified=False,
    # >= 10 chars: VerifyEmailRequest.token enforces min_length=10, so a
    # short literal like "tokdave" would 422 at validation, not 200.
    verification_token="tokdave123456", verification_token_expires=_FUTURE,
)
r = client.post("/auth/verify-email", json={"token": "tokdave123456"})
assert r.status_code == 200 and r.json()["verified"] is True
r = client.get("/auth/verify-email?token=doesnotexist")
assert r.status_code == 400, f"unknown token: {r.status_code}"
print("✅ POST body form verifies; unknown token → 400")

# 5e. Expired token → 400.
fake_db.create_user(
    "erin@example.com", "hash", plan="free", email_verified=False,
    verification_token="tokexpired",
    verification_token_expires="2020-01-01T00:00:00+00:00",  # long past
)
r = client.get("/auth/verify-email?token=tokexpired")
assert r.status_code == 400 and "expired" in r.json()["detail"].lower(), r.text
print("✅ expired token → 400")

# 5f. Resend → fresh token stored + email sent (targets an UNVERIFIED
# user: the verified-user short-circuit returns 400, and resend must never
# mint a token for someone who already verified).
with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
    r = client.post(
        "/auth/register",
        json={"email": "heidi@stratoscan.app", "password": "hunter2hunter2"},
    )
assert r.status_code == 200 and r.json()["email_sent"] is True, r.text
heidi_reg = r.json()
heidi = fake_db.get_user_by_email("heidi@stratoscan.app")
first_token = heidi["verification_token"]
with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
    r = client.post(
        "/auth/resend-verification",
        headers={"Authorization": f"Bearer {heidi_reg['access_token']}"},
    )
assert r.status_code == 200 and r.json()["email_sent"] is True, r.text
heidi2 = fake_db.get_user_by_email("heidi@stratoscan.app")
assert heidi2["verification_token"] and heidi2["verification_token"] != first_token
print("✅ resend issues a fresh token + email (unverified user)")

# 5f2. Resend for an ALREADY-VERIFIED user → 400 short-circuit, and NO
# email is attempted (mock would raise if urlopen is reached).
with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no email!")):
    r = client.post(
        "/auth/resend-verification",
        headers={"Authorization": f"Bearer {reg['access_token']}"},
    )
assert r.status_code == 400, f"verified resend must 400: {r.status_code} {r.text[:200]}"
print("✅ resend for verified user → 400 (already verified)")

# 5g. Login gate when EMAIL_VERIFICATION_REQUIRED=true. Use a REAL hash
# from the start (no dead first-login call with a bogus password — the
# only thing under test is the verification gate).
from src.auth import hash_password  # noqa: E402

with mock.patch("src.api.settings") as ms:
    ms.email_verification_required = True
    ms.jwt_secret = settings.jwt_secret
    ms.jwt_expire_hours = settings.jwt_expire_hours
    fake_db.create_user(
        "frank@example.com", hash_password("secret123"), plan="free",
        email_verified=False, verification_token="tokfrank",
        verification_token_expires=_FUTURE,
    )
    # Unverified user blocked → 403.
    r = client.post(
        "/auth/login",
        json={"email": "frank@example.com", "password": "secret123"},
    )
    assert r.status_code == 403, f"unverified login must be 403: {r.status_code} {r.text[:200]}"
    # Verified user logs in fine.
    fake_db.mark_email_verified(fake_db._users["frank@example.com"]["id"])
    r = client.post(
        "/auth/login",
        json={"email": "frank@example.com", "password": "secret123"},
    )
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True, r.text
print("✅ EMAIL_VERIFICATION_REQUIRED: unverified → 403, verified → 200")

# 5h. Register with verification DISABLED → immediately verified.
with mock.patch("src.api.settings") as ms:
    ms.has_jwt_secret = True
    ms.jwt_expire_hours = settings.jwt_expire_hours
    ms.has_email_verification = False
    ms.email_verification_required = False
    r = client.post(
        "/auth/register",
        json={"email": "grace@stratoscan.app", "password": "hunter2hunter2"},
    )
assert r.status_code == 200, r.text
assert r.json()["user"]["email_verified"] is True, "no SendGrid → no dangling unverified state"
assert r.json()["email_sent"] is False
print("✅ register without SendGrid → email_verified=True immediately")

# /auth/me reflects email_verified.
r = client.get(
    "/auth/me",
    headers={"Authorization": f"Bearer {reg['access_token']}"},
)
assert r.status_code == 200 and r.json()["email_verified"] is True, r.text
print("✅ /auth/me surfaces email_verified")

mock.patch.stopall()
print()
print("🎯 All email-verification tests pass!")
