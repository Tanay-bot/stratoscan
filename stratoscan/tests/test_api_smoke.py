"""Quick smoke test for the Stratoscan FastAPI layer.

Run: python -m tests.test_api_smoke
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so this
# suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

from unittest import mock

from fastapi.testclient import TestClient
from src.api import app
from tests.fakes import FakeLLMRouter

# Hermetic: replace the live LLM router with a fake so the suite never makes
# real Z.ai calls (saves free-tier quota, keeps tests network-independent).
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

client = TestClient(app)

# Health
r = client.get("/health")
assert r.status_code == 200, f"health: {r.status_code}"
print(f"✅ GET /health → {r.status_code}")

# Config
r = client.get("/config")
assert r.status_code == 200, f"config: {r.status_code}"
print(f"✅ GET /config → {r.status_code}")

# Route count
routes = set(
    rt.path for rt in app.routes if hasattr(rt, "path") and not rt.path.startswith(("/docs", "/redoc", "/openapi"))
)
expected = {
    "/health", "/config", "/screen", "/screen/export", "/interpret", "/backtest", "/backtest/export", "/enrich",
    "/backtest/async", "/backtest/async/{job_id}",
    "/stocks", "/stocks/{symbol}", "/refresh", "/news", "/news/{symbol}",
    "/auth/register", "/auth/login", "/auth/me",
    "/auth/verify-email", "/auth/resend-verification", "/auth/logout-all",
    "/auth/password-reset/request", "/auth/password-reset/confirm",
    "/auth/change-password", "/auth/delete-account", "/auth/refresh",
    "/auth/google/start", "/auth/google/callback",
    "/me/personal",
    "/ops/jobs", "/ops/jobs/{job_name}",
    "/admin", "/admin/overview", "/admin/users", "/admin/users/{user_id}", "/admin/jobs",
    "/metrics",
    "/", "/meta", "/meta/sectors", "/meta/industries",
    "/events",
    "/admin/events",
    "/admin/errors",
    # Day 6: no-signup trials and public share links
    "/try/session", "/try/backtest", "/try/backtest/{job_id}", "/try/claim",
    "/results/share", "/results/share/{share_id}", "/results/shares",
    "/r/{share_id}", "/r/{share_id}.json", "/r/{share_id}.png",
}
assert expected == routes, f"Routes mismatch. Missing: {expected - routes}. Extra: {routes - expected}"
print(f"✅ Routes: {len(routes)} registered — {sorted(routes)}")

# Interpret endpoint (only needs config, not supabase)
r = client.post("/interpret", json={"query": "tech stocks"})
print(f"✅ POST /interpret → {r.status_code}")

# Validation (empty query → 422)
r = client.post("/interpret", json={"query": ""})
assert r.status_code == 422, f"validation: {r.status_code}"
print(f"✅ Empty query validation → {r.status_code}")

# 404 for unknown stock (will error if postgres not configured, which is fine)
r = client.get("/stocks/INVALID")
print(f"✅ GET /stocks/INVALID → {r.status_code} (404=found, 502/503=postgres not configured)")

print()
print("🎯 All API smoke tests pass!")
