"""Hermetic auth tests for Stratoscan — no database, no network.

Run: python -m tests.test_auth

Covers:
1. Password hashing (PBKDF2 round-trip, wrong password, malformed hash)
2. JWT create/verify (valid, tampered, expired)
3. POST /auth/register (success, duplicate email → 409, bad email → 422)
4. POST /auth/login (success, wrong password → 401, unknown email → 401)
5. GET /auth/me (valid token, missing token, garbage token)
6. POST /backtest auth gate (no auth → 401, any authenticated user
   passes — the old premium paywall is gone; everything is free)

The DB is swapped for :class:`tests.fakes.FakeUserDB` and the LLM router
for :class:`tests.fakes.FakeLLMRouter`, so nothing ever leaves the box.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

# Rate limits OFF for this suite: the auth bucket is 10/min and the test
# makes exactly 10 auth calls + 3 backtest calls (2/min bucket) — running
# with limits enabled would 429 the tail end of the suite. Auth logic is
# what's under test here; the limit NUMBERS are locked in test_rate_limits.
os.environ["RATE_LIMIT_ENABLED"] = "false"

import src.auth as auth_module
from fastapi.testclient import TestClient
from src.auth import (
    hash_password,
    verify_password,
    create_access_token,
    verify_access_token,
)
from tests.fakes import FakeUserDB, FakeLLMRouter

# ── Hermetic setup: fake DB + fake LLM ──────────────────────────────────────
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app

client = TestClient(app)

# ── 1. Password hashing ──────────────────────────────────────────────────────
print("1. Password hashing (PBKDF2)...")

h = hash_password("S3cure!Pass")
assert h.startswith("pbkdf2_sha256$600000$"), f"bad hash format: {h[:40]}"
assert verify_password("S3cure!Pass", h), "round-trip failed"
assert not verify_password("WrongPass1", h), "wrong password accepted!"
assert not verify_password("S3cure!Pass", "not-a-valid-hash"), "malformed hash accepted!"
print("✅ hash round-trip + rejection works")

# Two hashes of the same password differ (random salt)
assert hash_password("same") != hash_password("same"), "salt not random!"
print("✅ per-user salt is random")

# ── 2. JWT tokens ────────────────────────────────────────────────────────────
print("2. JWT create/verify...")

token = create_access_token(42, "me@stratoscan.app")
payload = verify_access_token(token)
assert payload is not None, "valid token rejected"
assert payload["sub"] == "42", f"sub mismatch: {payload['sub']}"
assert payload["email"] == "me@stratoscan.app"
print("✅ valid token verifies")

assert verify_access_token(token + "x") is None, "tampered token accepted!"
print("✅ tampered token rejected")

# Expired token (patch the module-level expire setting to force expiry)
original_expire = auth_module.settings.jwt_expire_hours
try:
    auth_module.settings.jwt_expire_hours = -1  # token already expired
    expired = create_access_token(1, "x@y.z")
    assert verify_access_token(expired) is None, "expired token accepted!"
finally:
    auth_module.settings.jwt_expire_hours = original_expire
print("✅ expired token rejected")

# ── 3. Register ──────────────────────────────────────────────────────────────
print("3. POST /auth/register...")

r = client.post("/auth/register", json={"email": "Alice@Stratoscan.App", "password": "hunter2hunter2"})
assert r.status_code == 200, f"register: {r.status_code} {r.text[:200]}"
body = r.json()
assert body["token_type"] == "bearer"
assert body["access_token"], "no access token returned"
assert body["user"]["email"] == "alice@stratoscan.app", "email not normalized"
assert body["user"]["plan"] == "free", "new users must start free"
print(f"✅ register → 200, plan={body['user']['plan']}")

r = client.post("/auth/register", json={"email": "alice@stratoscan.app", "password": "another-pass"})
assert r.status_code == 409, f"duplicate register: {r.status_code} (want 409)"
print("✅ duplicate email → 409")

r = client.post("/auth/register", json={"email": "not-an-email", "password": "hunter2hunter2"})
assert r.status_code == 422, f"bad email: {r.status_code} (want 422)"
print("✅ invalid email → 422")

r = client.post("/auth/register", json={"email": "x@y.z", "password": "short"})
assert r.status_code == 422, f"short password: {r.status_code} (want 422)"
print("✅ short password → 422")

# ── 4. Login ─────────────────────────────────────────────────────────────────
print("4. POST /auth/login...")

r = client.post("/auth/login", json={"email": "alice@stratoscan.app", "password": "hunter2hunter2"})
assert r.status_code == 200, f"login: {r.status_code} {r.text[:200]}"
assert r.json()["access_token"], "no token on login"
print("✅ correct credentials → 200 + token")

r = client.post("/auth/login", json={"email": "alice@stratoscan.app", "password": "wrong-password"})
assert r.status_code == 401, f"wrong password: {r.status_code} (want 401)"
print("✅ wrong password → 401")

r = client.post("/auth/login", json={"email": "nobody@stratoscan.app", "password": "whatever123"})
assert r.status_code == 401, f"unknown email: {r.status_code} (want 401)"
print("✅ unknown email → 401 (no user enumeration)")

# ── 5. /auth/me ──────────────────────────────────────────────────────────────
print("5. GET /auth/me...")

r = client.get("/auth/me")
assert r.status_code == 401, f"me w/o token: {r.status_code} (want 401)"
print("✅ no token → 401")

r = client.get("/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
assert r.status_code == 401, f"me w/ garbage: {r.status_code} (want 401)"
print("✅ garbage token → 401")

r = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
assert r.status_code == 200, f"me w/ token: {r.status_code} {r.text[:200]}"
assert r.json()["email"] == "alice@stratoscan.app"
assert r.json()["plan"] == "free"
assert "password" not in r.text.lower(), "LEAKED password hash in /auth/me!"
print("✅ valid token → 200 (no password leak)")

# ── 6. /backtest auth gate (no paywall — everything is free) ───────────────
print("6. POST /backtest auth gate...")

# The premium paywall was removed: /backtest is free for every authenticated
# user. Auth still applies for per-user rate limiting (no token → 401).
r = client.post("/backtest", json={"query": "tech stocks", "top_n": 2})
assert r.status_code == 401, f"backtest w/o auth: {r.status_code} (want 401)"
print("✅ no auth → 401")

# Fake the backtest pipeline (interpreter + engine) so the test stays instant.
class _FakeBacktestEngine:
    # Mirror the real BacktestEngine signature (Tier 2 added strategy_rules;
    # Tier 4 start_date/end_date; Tier 1 max_sector_exposure; Tier 3 symbols
    # + progress_cb) so the endpoint's call is accepted.
    def backtest_top_results(
        self, interpreted, top_n=5, strategy_rules=None,
        start_date=None, end_date=None, max_sector_exposure=None,
        symbols=None, progress_cb=None,
    ):
        return {}

with mock.patch("src.interpreter.interpret_query") as mock_interpret, mock.patch(
    "src.backtester.BacktestEngine", _FakeBacktestEngine
):
    mock_interpret.return_value.sector = "Technology"
    mock_interpret.return_value.industry = None
    mock_interpret.return_value.momentum = "rising"
    mock_interpret.return_value.filters = []
    mock_interpret.return_value.error = None

    # Any authenticated user (free plan) passes — no 402, no premium needed.
    r = client.post(
        "/backtest",
        json={"query": "tech stocks", "top_n": 2},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
assert r.status_code == 200, f"free user backtest: {r.status_code} {r.text[:200]}"
print("✅ free user → 200 (no paywall)")

print()
print("🎯 All auth tests pass!")
