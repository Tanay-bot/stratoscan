"""Hermetic tests for session revocation (users.token_version) — no DB, no network.

A token used to be valid until it expired, full stop. ``create_access_token``
now stamps the user's ``token_version`` as the ``ver`` claim, and
``get_current_user`` rejects any token whose claim doesn't match the row — so
incrementing the column (POST /auth/logout-all) invalidates every token the
account ever issued, instantly, with no blocklist and no extra query (the row
is already loaded on every authenticated request).

Two assertions are what make the rest mean anything, because a check wired to
reject unconditionally passes every other case here:

  - a token issued AFTER the bump still works (section 2);
  - a bump on one account leaves another account's token alone (section 7).

Section 4 is the other trap: a token with no ``ver`` claim is only tested
against an account whose real version IS 0, so a 401 there can only come from
``payload.get("ver")`` being ``None`` — a ``.get("ver", 0)`` implementation
would return 200 and is caught.

``FakeUserDB`` mirrors the real method surface — including
``bump_token_version`` and the ``token_version`` key on every stored row —
because a fake user missing that key would take the ``.get(..., 0)`` default
and mask a broken comparison (spec 008, trap 1).

Run: python -m tests.test_token_revocation
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Rate limits OFF: this suite deliberately makes far more /auth calls than the
# 10/min auth bucket allows, and the bucket numbers are not what is under test
# (test_rate_limits pins those).
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "token-revocation-dummy-secret")
# No outbound mail: registration must never attempt SendGrid or SMTP.
os.environ["SENDGRID_API_KEY"] = ""
os.environ["EMAIL_FROM_ADDRESS"] = ""

from unittest import mock

from fastapi.testclient import TestClient
from src.auth import verify_access_token
from src.config import settings
from tests.fakes import FakeLLMRouter, FakeUserDB

# ── Hermetic setup: fake DB + fake LLM ──────────────────────────────────────
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app  # noqa: E402  (env must be pinned before the import)

client = TestClient(app)

PASSWORD = "hunter2hunter2"


def _register(email: str) -> str:
    """Create an account through the REAL endpoint and return its token."""
    r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, f"register {email}: {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


def _login(email: str) -> str:
    """Mint a fresh token through the REAL endpoint (stamped with the row's
    current version — the issuance site that has to read the column)."""
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


def _me(token: str):
    """Hit an authed endpoint with a token."""
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


def _logout_all(token: str):
    """POST /auth/logout-all with a token."""
    return client.post(
        "/auth/logout-all", headers={"Authorization": f"Bearer {token}"}
    )


def _version(email: str) -> int:
    return fake_db.get_user_by_email(email)["token_version"]


def _legacy_token(user_id: int, email: str) -> str:
    """A VALIDLY SIGNED token in the pre-008 shape: no ``ver`` claim.

    Hand-built rather than derived from ``create_access_token``, because the
    point is the payload that existed before the claim did. Its signature and
    expiry verify normally, so the only thing wrong with it is the missing
    claim — which is what makes the 401 below a test of the rule rather than
    of a malformed token.
    """

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + settings.jwt_expire_hours * 3600,
    }
    signing_input = (
        f"{b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())}."
        f"{b64(json.dumps(payload).encode())}"
    )
    signature = hmac.new(
        settings.jwt_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{b64(signature)}"


# ═══════════════════════════════════════════════════════════════════════════
# 1. The round trip: a live token, one bump, the same token dead
# ═══════════════════════════════════════════════════════════════════════════
print("1. Bump revokes the token that made the call...")
ALICE = "alice@stratoscan.app"
alice_token = _register(ALICE)
# The 200 BEFORE the bump is what makes the 401 after it mean something.
assert _me(alice_token).status_code == 200, "the token must work before the bump"
assert _version(ALICE) == 0, "a fresh account starts at version 0"

r = _logout_all(alice_token)
assert r.status_code == 200, f"logout-all: {r.status_code} {r.text[:200]}"
body = r.json()
assert body["error"] is False, body
assert body["token_version"] == 1, body
assert isinstance(body["message"], str) and body["message"], body
print("✅ POST /auth/logout-all → 200 {error: false, token_version: 1}")

after = _me(alice_token)
assert after.status_code == 401, f"same token after the bump: {after.status_code} (want 401)"
# The rejection must not explain itself: a holder of a stolen token learns
# nothing about why it stopped working, and the internal column name stays out
# of the response.
detail = after.json().get("detail", "")
for leak in ("token_version", "version", "bump", "revoc", "logout"):
    assert leak not in detail.lower(), f"401 detail leaks {leak!r}: {detail}"
print("✅ the SAME token → 401 with a generic detail (no reason, no column name)")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Issuance after the bump — the discriminating half
# ═══════════════════════════════════════════════════════════════════════════
print("2. A token minted after the bump still works...")
alice_token2 = _login(ALICE)
assert verify_access_token(alice_token2)["ver"] == 1, "login must stamp the ROW's version"
assert _me(alice_token2).status_code == 200, "a fresh login must not be rejected"
assert _me(alice_token).status_code == 401, "the pre-bump token must stay dead"
print("✅ login after the bump → ver=1 → 200 (the old token stays dead)")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Two devices, one bump: both die
# ═══════════════════════════════════════════════════════════════════════════
print("3. Two devices, one bump: both die...")
phone = _login(ALICE)
laptop = _login(ALICE)
assert _me(phone).status_code == 200 and _me(laptop).status_code == 200, "both live first"

r = _logout_all(phone)
assert r.status_code == 200 and r.json()["token_version"] == 2, r.text[:200]
assert _me(phone).status_code == 401, "the calling device must be revoked too"
assert _me(laptop).status_code == 401, "the OTHER device must be revoked as well"
print("✅ one bump (→2) kills both live tokens, including the caller's own")


# ═══════════════════════════════════════════════════════════════════════════
# 4. A token with no `ver` at all → 401, never silently grandfathered
# ═══════════════════════════════════════════════════════════════════════════
print("4. A pre-008 token (no ver claim) is rejected...")
DAVE = "dave@stratoscan.app"
_register(DAVE)
dave_id = fake_db.get_user_by_email(DAVE)["id"]
# Dave's real version is 0, which is the whole point: if the check were
# ``payload.get("ver", 0)`` (a permanently unrevocable pre-change token) this
# token would be accepted. It 401s only because the missing claim is compared
# as None.
assert fake_db.get_user_by_id(dave_id)["token_version"] == 0
legacy = _legacy_token(dave_id, DAVE)
assert verify_access_token(legacy) is not None, "the legacy token must still verify (signature + expiry)"
r = _me(legacy)
assert r.status_code == 401, f"no-ver token: {r.status_code} (want 401)"
print("✅ signature-valid token without 'ver' → 401 (missing claim is not defaulted to 0)")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Two bumps in a row → version 2; the version-1 token is still dead
# ═══════════════════════════════════════════════════════════════════════════
print("5. Two bumps in a row...")
BOB = "bob@stratoscan.app"
bob_token = _register(BOB)
r1 = _logout_all(bob_token)
assert r1.status_code == 200 and r1.json()["token_version"] == 1, r1.text[:200]

v1_token = _login(BOB)  # minted at version 1
assert verify_access_token(v1_token)["ver"] == 1
assert _me(v1_token).status_code == 200, "the ver=1 token must be live before the second bump"

r2 = _logout_all(v1_token)
assert r2.status_code == 200 and r2.json()["token_version"] == 2, r2.text[:200]
assert _version(BOB) == 2, _version(BOB)
assert _me(v1_token).status_code == 401, "a version-1 token must not survive to version 2"

v2_token = _login(BOB)
assert verify_access_token(v2_token)["ver"] == 2
assert _me(v2_token).status_code == 200, "the current version must still work"
assert _me(bob_token).status_code == 401, "the original version-0 token is still dead"
print("✅ bump → 1 → 2 through the route; ver=0 and ver=1 tokens are 401, ver=2 is 200")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Missing / malformed credentials → 401, not 500
# ═══════════════════════════════════════════════════════════════════════════
print("6. /auth/logout-all without a valid token...")
r = client.post("/auth/logout-all")
assert r.status_code == 401, f"no token: {r.status_code} (want 401)"
r = client.post("/auth/logout-all", headers={"Authorization": "Bearer garbage.token.here"})
assert r.status_code == 401, f"garbage token: {r.status_code} (want 401)"
print("✅ no token / garbage token → 401 (not 500), nothing bumped")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Revocation is per-account — the other discriminating half
# ═══════════════════════════════════════════════════════════════════════════
print("7. A bump on one account leaves another account alone...")
CAROL = "carol@stratoscan.app"
carol_token = _register(CAROL)  # version 0
victim_token = _login(ALICE)  # alice is at version 2
assert _me(carol_token).status_code == 200, "carol must be live first"

r = _logout_all(victim_token)
assert r.status_code == 200 and r.json()["token_version"] == 3, r.text[:200]
assert _me(victim_token).status_code == 401, "alice's token must die"
assert _me(carol_token).status_code == 200, "CAROL's token must be untouched"
print("✅ alice's bump (→3) kills alice's token; carol's still works")

print()
print("🎯 All token revocation tests pass!")
