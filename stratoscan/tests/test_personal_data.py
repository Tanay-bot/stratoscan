"""Hermetic tests for the /me/personal endpoints (watchlist/screeners/backtests).

Run: python -m tests.test_personal_data

Covers:
1. GET /me/personal requires auth (401 without token)
2. GET /me/personal returns empty arrays for a fresh user (no 404)
3. PUT /me/personal persists watchlist/screeners/backtests
4. GET /me/personal returns what was PUT (round-trip)
5. PUT normalizes watchlist symbols to uppercase
6. PUT replaces (not merges) the whole payload

The DB is swapped for tests.fakes.FakeUserDB (which now mirrors
get_user_data / upsert_user_data) and the LLM router is faked, so nothing
ever leaves the box.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock

# Rate limits OFF for this suite (same reasoning as test_auth.py).
os.environ["RATE_LIMIT_ENABLED"] = "false"

from fastapi.testclient import TestClient
from tests.fakes import FakeUserDB, FakeLLMRouter

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app

client = TestClient(app)


def _register() -> dict:
    """Register a throwaway user and return their auth header + token."""
    r = client.post(
        "/auth/register",
        json={"email": f"p{abs(hash(()) % 10**6)}@test.dev", "password": "hunter2hunter2"},
    )
    assert r.status_code == 200, f"register: {r.status_code} {r.text[:200]}"
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


print("1. GET /me/personal requires auth...")
r = client.get("/me/personal")
assert r.status_code == 401, f"no token: {r.status_code} (want 401)"
print("✅ no token → 401")

print("2. Fresh user gets empty arrays (no 404)...")
headers = _register()
r = client.get("/me/personal", headers=headers)
assert r.status_code == 200, f"fresh: {r.status_code} {r.text[:200]}"
body = r.json()
assert body["watchlist"] == [], f"watchlist: {body['watchlist']}"
assert body["saved_screeners"] == [], f"screeners: {body['saved_screeners']}"
assert body["recent_backtests"] == [], f"backtests: {body['recent_backtests']}"
print("✅ empty arrays for fresh user")

print("3. PUT persists personal data...")
payload = {
    "watchlist": ["NVDA", "AAPL"],
    "saved_screeners": [{"id": "scr-1", "query": "momentum breakout stocks", "savedAt": "2026-08-03T00:00:00Z"}],
    "recent_backtests": [{"query": "oversold tech", "savedAt": "2026-08-03T00:00:00Z", "total_symbols_tested": 2}],
}
r = client.put("/me/personal", headers=headers, json=payload)
assert r.status_code == 200, f"put: {r.status_code} {r.text[:200]}"
print("✅ PUT → 200")

print("4. GET returns what was PUT (round-trip)...")
r = client.get("/me/personal", headers=headers)
assert r.status_code == 200
body = r.json()
assert body["watchlist"] == ["NVDA", "AAPL"], f"watchlist: {body['watchlist']}"
assert body["saved_screeners"][0]["query"] == "momentum breakout stocks"
assert body["recent_backtests"][0]["query"] == "oversold tech"
print("✅ round-trip OK")

print("5. Watchlist symbols normalized to uppercase...")
r = client.put("/me/personal", headers=headers, json={"watchlist": ["nvda", "meta"]})
body = r.json()
assert body["watchlist"] == ["NVDA", "META"], f"normalize: {body['watchlist']}"
print("✅ lowercase → uppercase")

print("6. PUT replaces (not merges)...")
r = client.put("/me/personal", headers=headers, json={"watchlist": ["GE"]})
body = r.json()
assert body["watchlist"] == ["GE"], f"replace: {body['watchlist']}"
assert body["saved_screeners"] == [], "replace must drop prior screeners"
print("✅ PUT replaces the whole payload")

print()
print("🎯 All personal-data tests pass!")
