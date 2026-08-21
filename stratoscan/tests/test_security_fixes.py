"""Regression tests for the three verified security findings.

Run: python -m tests.test_security_fixes

These tests FAIL against the current code and PASS after the fixes:

  VULN-001  login timing equalization — verify_password must be invoked even
            when the email does not exist (a fixed dummy hash, never the real
            short-circuit), so response time can't reveal account existence.
  VULN-002  register email burn — /auth/register is throttled by its OWN
            dedicated scope (tighter than the shared auth bucket) so mass
            signup can't exhaust the SendGrid free tier (100/day).
  VULN-003  register account oracle — the same dedicated register scope makes
            mass account enumeration impractical at scale.

Hermetic: no DB, no network, no real Redis. The DB is FakeUserDB, the LLM is
FakeLLMRouter, and rate-limit caps are patched to tiny values on a scratch app.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import src.api as api_module  # noqa: E402
from src.api import (  # noqa: E402
    RATE_LIMITS,
    GLOBAL_LIMITS,
    SCOPE_GLOBAL,
    RateLimitMiddleware,
    _scope_for_path,
)
from src.redis_ratelimit import InMemoryRateLimitStore  # noqa: E402
from tests.fakes import FakeUserDB, FakeLLMRouter  # noqa: E402

failures = []
def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(name)

# ═════════════════════════════════════════════════════════════════════════════
print("=== VULN-001: login must burn PBKDF2 even for unknown emails ===")
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app  # noqa: E402
client = TestClient(app)
client.post("/auth/register", json={"email": "v1@test.app", "password": "hunter2hunter2"})

# Patch verify_password INSIDE src.auth (the login handler imports it from there).
with mock.patch("src.auth.verify_password", wraps=None) as spy:
    # We can't easily wrap (function import path), so count calls via a side-effect.
    calls = []
    def _spy(password, stored_hash):
        calls.append(stored_hash)
        # Fail closed: never let the test actually log in.
        return False
    spy.side_effect = _spy

    r_known = client.post("/auth/login", json={"email": "v1@test.app", "password": "wrong"})
    n_after_known = len(calls)
    r_unknown = client.post("/auth/login", json={"email": "ghost@test.app", "password": "wrong"})
    n_after_unknown = len(calls)

check("VULN-001: existing-email login still 401 (control)", r_known.status_code == 401)
check("VULN-001: verify_password called for KNOWN email (wrong pw)", n_after_known >= 1, f"calls={n_after_known}")
check(
    "VULN-001: verify_password called for UNKNOWN email (no short-circuit)",
    n_after_unknown > n_after_known,
    f"known={n_after_known} unknown_total={n_after_unknown}",
)
# The dummy hash must be a real pbkdf2 string so the cost is identical.
if len(calls) > n_after_known:
    dummy = calls[n_after_known]
    check("VULN-001: unknown-email verify uses a real PBKDF2 dummy hash",
          dummy.startswith("pbkdf2_sha256$"), dummy[:40])
mock.patch.stopall()

# ═════════════════════════════════════════════════════════════════════════════
print("\n=== VULN-002/003: /auth/register has a DEDICATED, tighter scope ===")
check("VULN-002: /auth/register maps to its own 'register' scope",
      _scope_for_path("/auth/register") == "register",
      f"got={_scope_for_path('/auth/register')}")
check("VULN-002: 'register' scope exists in RATE_LIMITS",
      "register" in RATE_LIMITS, str(list(RATE_LIMITS.keys())))
if "register" in RATE_LIMITS:
    reg = RATE_LIMITS["register"]
    auth = RATE_LIMITS.get("auth", {})
    check("VULN-002: register caps are TIGHTER than the shared auth bucket",
          reg["burst"] < auth["burst"] and reg["daily"] < auth["daily"],
          f"register={reg} auth={auth}")
check("VULN-002: register feeds a GLOBAL cap (planet-wide daily budget)",
      SCOPE_GLOBAL.get("register") in GLOBAL_LIMITS,
      f"scope={SCOPE_GLOBAL.get('register')} globals={list(GLOBAL_LIMITS.keys())}")

# ═════════════════════════════════════════════════════════════════════════════
print("\n=== VULN-002 behavioral: register throttled at the dedicated cap ===")
def make_scratch(register_caps):
    scratch = FastAPI()
    scratch.add_middleware(RateLimitMiddleware)

    @scratch.post("/auth/register")
    async def stub_register(request: Request):
        return {"ok": True}

    store = InMemoryRateLimitStore()
    mock.patch.object(api_module, "get_rate_limit_store", return_value=store).start()
    mock.patch.dict(api_module.RATE_LIMITS, {"register": register_caps}).start()
    return scratch

original = dict(RATE_LIMITS)
try:
    caps = {"burst": 2, "daily": 5, "weekly": 10}
    scratch = make_scratch(caps)
    scw = TestClient(scratch, base_url="http://reg.test")
    codes = [scw.post("/auth/register").status_code for _ in range(4)]
    check("VULN-002: 1st+2nd register OK, 3rd+4th 429 (burst=2)",
          codes[:2] == [200, 200] and codes[2:] == [429, 429], f"codes={codes}")
    r429 = scw.post("/auth/register")
    check("VULN-002: 429 carries Retry-After", r429.status_code == 429 and r429.headers.get("retry-after"),
          f"retry-after={r429.headers.get('retry-after')}")
finally:
    mock.patch.stopall()
    api_module.RATE_LIMITS.clear()
    api_module.RATE_LIMITS.update(original)

print()
if failures:
    print(f"FAILED ({len(failures)}): {failures}")
    sys.exit(1)
print("🎯 All security-fix regression tests pass!")
