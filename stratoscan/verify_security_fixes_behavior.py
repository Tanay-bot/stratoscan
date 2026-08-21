"""Behavioral verification of the three security fixes — drives the REAL app.

Run: python verify_security_fixes_behavior.py

Unlike the unit suites (which lock the contract), this probe exercises the
actual request lifecycle end-to-end through TestClient and ASSERTS the
observable state transitions:

  A. VULN-001 /refresh gate
       unauth → 401/403 AND no background job scheduled (add_task never
       called); admin  → 200 AND add_task called once (legit flow works).
  B. refresh_ops global cap actually trips
       cap shrunk to 2 → 3rd admin /refresh in the same window → 429.
  C. VULN-002 no exception text crosses the API boundary
       forced internal errors on /screen, /auth/register, /backtest →
       502 with generic detail (markers absent); generic 500 handler →
       generic message (marker absent).
  D. VULN-003 /ops/* gate
       unauth → 401; admin → 200 with the real job list; {job_name} →
       200; unknown → 404.

Hermetic: FakeUserDB + FakeLLMRouter, rate limiter on the IN-MEMORY store
(REDIS_RATE_LIMIT_ENABLED=false), BackgroundTasks patched so no real
Finnhub thread ever starts, JWT minted directly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# Rate limiter ON (in-memory backend) so the cap-trip section is real;
# Redis OFF so no network. Must precede the src.api import.
os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
admin_id = fake_db.create_user(
    "ops-admin@stratoscan.app", "x", plan="free",
    email_verified=True, is_admin=True,
)
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import (  # noqa: E402
    GLOBAL_LIMITS,
    RATE_LIMITS,
    SCOPE_GLOBAL,
    app,
    generic_exception_handler,
)
import src.api as api_module  # noqa: E402
from src.auth import create_access_token  # noqa: E402
from src.redis_ratelimit import InMemoryRateLimitStore  # noqa: E402
from starlette.requests import Request  # noqa: E402


# The middleware counts EVERY request (including ones that later 401 — it
# runs before auth), and the REAL refresh bucket is burst=1/min + global
# refresh_ops=3/day. Each rate-limit section therefore gets a FRESH store
# (patched on src.api.get_rate_limit_store, which the middleware resolves
# per-request) and probe-raised caps, so section A isn't tripped by section
# B's consumption and vice versa. The REAL values are pinned in
# tests/test_rate_limits.py; this probe measures behavior, not numbers.
def _refresh_caps():
    return {"burst": 10, "daily": 50, "weekly": 50}

client = TestClient(app)
admin_headers = {
    "Authorization": f"Bearer {create_access_token(admin_id, 'ops-admin@stratoscan.app')}"
}

print("=" * 70)
print("SECURITY FIXES — behavioral lifecycle verification")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════════════════
# A. VULN-001: /refresh auth gate + job scheduling lifecycle
# ═══════════════════════════════════════════════════════════════════════════════
print("\nA. POST /refresh gate + job-scheduling lifecycle...")

with mock.patch(
    "src.api.get_rate_limit_store", return_value=InMemoryRateLimitStore()
), mock.patch.object(
    api_module, "RATE_LIMITS", {**RATE_LIMITS, "refresh": _refresh_caps()}
), mock.patch.object(
    api_module, "GLOBAL_LIMITS", {**GLOBAL_LIMITS, "refresh_ops": 100}
):
    with mock.patch("src.api.BackgroundTasks.add_task") as _add_task:
        r_unauth = client.post("/refresh")
        assert r_unauth.status_code in (401, 403), (
            f"unauth /refresh: {r_unauth.status_code} (want 401/403) {r_unauth.text[:200]}"
        )
        assert not _add_task.called, (
            "background job scheduled for an UNAUTHENTICATED caller — VULN-001"
        )
    print(f"   unauth → {r_unauth.status_code}, no job scheduled ✅")

    with mock.patch("src.api.BackgroundTasks.add_task") as _add_task:
        r_admin = client.post("/refresh", headers=admin_headers)
        assert r_admin.status_code == 200, (
            f"admin /refresh: {r_admin.status_code} {r_admin.text[:300]}"
        )
        assert _add_task.call_count == 1, (
            f"admin /refresh should schedule exactly one job, add_task called {_add_task.call_count}x"
        )
    print(f"   admin → {r_admin.status_code}, exactly 1 job scheduled ✅")

# ═══════════════════════════════════════════════════════════════════════════════
# B. VULN-001 defense-in-depth: refresh_ops global cap actually trips
# ═══════════════════════════════════════════════════════════════════════════════
print("\nB. refresh_ops global cap (shrunken to 2) trips on the real route...")
assert SCOPE_GLOBAL.get("refresh") == "refresh_ops", (
    f"/refresh must map to the refresh_ops global bucket, got {SCOPE_GLOBAL.get('refresh')}"
)
real_global = dict(GLOBAL_LIMITS)
real_global["refresh_ops"] = 2  # shrink for the probe; the real value is pinned in tests
with mock.patch(
    "src.api.get_rate_limit_store", return_value=InMemoryRateLimitStore()
), mock.patch.object(api_module, "GLOBAL_LIMITS", real_global), mock.patch.object(
    api_module, "RATE_LIMITS", {**RATE_LIMITS, "refresh": _refresh_caps()}
), mock.patch("src.api.BackgroundTasks.add_task"):
    codes = [
        client.post("/refresh", headers=admin_headers).status_code
        for _ in range(4)
    ]
assert codes[:2] == [200, 200], f"first two admin refreshes should pass: {codes}"
assert codes[2] == 429 and codes[3] == 429, (
    f"3rd+ admin refresh must hit the global cap (429), got {codes}"
)
print(f"   statuses across 4 admin refreshes: {codes} → cap trips at #3 ✅")

# ═══════════════════════════════════════════════════════════════════════════════
# C. VULN-002: no exception text crosses the API boundary
# ═══════════════════════════════════════════════════════════════════════════════
print("\nC. Error surfaces return generic details (no exception text)...")

# C1. /screen endpoint-level 502
with mock.patch(
    "src.api._run_screen", side_effect=ValueError("SCREENING_LEAK_AAA")
):
    r = client.post("/screen", json={"query": "leak"})
assert r.status_code == 502, f"/screen forced error: {r.status_code}"
assert "SCREENING_LEAK_AAA" not in r.text, r.text
print(f"   /screen forced error → 502, detail generic ({r.json().get('detail')!r}) ✅")

# C2. /auth/register 502
with mock.patch.object(
    fake_db, "create_user", side_effect=Exception("POSTGRES_LEAK_BBB")
):
    r = client.post(
        "/auth/register",
        json={"email": "leak@example.com", "password": "S3cure!Pass123"},
    )
assert r.status_code == 502, f"register forced error: {r.status_code}"
assert "POSTGRES_LEAK_BBB" not in r.text, r.text
print(f"   register forced error → 502, detail generic ({r.json().get('detail')!r}) ✅")

# C3. /backtest interpretation failure 502 (interpreted.error carries exception text)
with mock.patch("src.interpreter.interpret_query") as _mq:
    _mq.return_value.error = "LLM call failed: BACKTEST_LEAK_CCC"
    r = client.post(
        "/backtest",
        json={"query": "leak", "top_n": 2},
        headers=admin_headers,
    )
assert r.status_code == 502, f"backtest interpret error: {r.status_code}"
assert "BACKTEST_LEAK_CCC" not in r.text, r.text
print(f"   /backtest interpret error → 502, detail generic ({r.json().get('detail')!r}) ✅")

# C4. generic 500 catch-all
import asyncio  # noqa: E402

_request = mock.Mock(spec=Request)
_response = asyncio.run(
    generic_exception_handler(_request, ValueError("GENERIC_LEAK_DDD"))
)
body = _response.body.decode("utf-8")
assert "GENERIC_LEAK_DDD" not in body, body
print(f"   generic 500 handler → generic body ({body!r}) ✅")

# ═══════════════════════════════════════════════════════════════════════════════
# D. VULN-003: /ops/* admin-only, job telemetry still readable by admins
# ═══════════════════════════════════════════════════════════════════════════════
print("\nD. /ops/jobs gate + job telemetry lifecycle...")

r0 = client.get("/ops/jobs")
assert r0.status_code == 401, f"unauth /ops/jobs: {r0.status_code} (want 401)"
print(f"   unauth → {r0.status_code} ✅")

from src.job_monitor import (  # noqa: E402
    JOB_DEFINITIONS,
    InMemoryJobMonitorStore,
    JobMonitor,
)

seed = InMemoryJobMonitorStore()
_runner = JobMonitor(seed)
with _runner.run("price_refresh") as run:
    run.records = 42
    run.details = "quotes only"

with mock.patch("src.job_monitor.get_job_monitor_store", return_value=seed):
    r = client.get("/ops/jobs", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    names = {j["job_name"] for j in body["jobs"]}
    assert names == set(JOB_DEFINITIONS), names
    price = next(j for j in body["jobs"] if j["job_name"] == "price_refresh")
    assert price["last_status"] == "success" and price["last_records"] == 42, price
    print(f"   admin → 200, {len(names)} jobs, price_refresh records=42 ✅")

    r2 = client.get("/ops/jobs/price_refresh", headers=admin_headers)
    assert r2.status_code == 200 and r2.json()["job_name"] == "price_refresh", r2.text
    print("   admin /ops/jobs/{job_name} → 200 ✅")

    r3 = client.get("/ops/jobs/nonexistent_job", headers=admin_headers)
    assert r3.status_code == 404, r3.text
    print("   unknown job → 404 ✅")

    # Security cross-check: /ops/jobs must NOT be reachable by a NON-admin user.
    non_admin_id = fake_db.create_user(
        "bob@stratoscan.app", "y", plan="free", email_verified=True, is_admin=False,
    )
    bob_headers = {
        "Authorization": f"Bearer {create_access_token(non_admin_id, 'bob@stratoscan.app')}"
    }
    r4 = client.get("/ops/jobs", headers=bob_headers)
    assert r4.status_code == 403, f"non-admin /ops/jobs: {r4.status_code} (want 403)"
    print(f"   non-admin → {r4.status_code} (role check enforced) ✅")

print()
print("🎯 All security-fix behavioral checks pass!")
