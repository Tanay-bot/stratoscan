"""Import and smoke-test all Stratoscan modules including the FastAPI API layer.

Run: python -m tests.test_imports
"""

from __future__ import annotations

import sys
import os
from unittest import mock

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so the
# smoke section never consumes real Upstash shared buckets (a 429 on
# /backtest or /screen would break the assertions below).
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"


def main() -> int:
    """Run all import checks and return 0 on success."""
    print("=" * 60)
    print("Stratoscan — Import & Smoke Test Suite")
    print("=" * 60)

    results = []

    # ── Core Modules ──────────────────────────────────────────────────
    print("\n1. Checking core modules...")

    try:
        from src.config import settings
        results.append(("config", f"Finnhub keys: {settings.finnhub_key_count}", True))
    except Exception as e:
        results.append(("config", str(e), False))

    try:
        from src.interpreter import InterpretedQuery, FilterCriterion
        results.append(("interpreter", "OK", True))
    except Exception as e:
        results.append(("interpreter", str(e), False))

    try:
        from src.screener import StockScreener, ScreenerResult
        results.append(("screener", "OK", True))
    except Exception as e:
        results.append(("screener", str(e), False))

    try:
        from src.backtester import BacktestEngine, BacktestResult
        results.append(("backtester", "OK", True))
    except Exception as e:
        results.append(("backtester", str(e), False))

    try:
        from src.enrichment import EnrichmentEngine
        results.append(("enrichment", "OK", True))
    except Exception as e:
        results.append(("enrichment", str(e), False))

    try:
        from src.data_fetcher import FinnhubClientPool, initialize_client_pool
        results.append(("data_fetcher", "OK", True))
    except Exception as e:
        results.append(("data_fetcher", str(e), False))

    try:
        from src.database import PostgresDB, NewsDB, BacktestDB, SQLiteDB
        results.append(("database", "OK", True))
    except Exception as e:
        results.append(("database", str(e), False))

    try:
        from src.scheduler import DataRefreshScheduler
        results.append(("scheduler", "OK", True))
    except Exception as e:
        results.append(("scheduler", str(e), False))

    try:
        from src.job_monitor import (
            JOB_DEFINITIONS,
            JobMonitor,
            InMemoryJobMonitorStore,
            PostgresJobMonitorStore,
        )
        results.append(("job_monitor", f"OK ({len(JOB_DEFINITIONS)} jobs)", True))
    except Exception as e:
        results.append(("job_monitor", str(e), False))

    try:
        from src.cache_warmer import warm_backtest_cache, build_backtest_history_cache
        results.append(("cache_warmer", "OK", True))
    except Exception as e:
        results.append(("cache_warmer", str(e), False))

    try:
        from src.emails import (
            generate_verification_token,
            build_verification_url,
            send_email,
            send_verification_email,
        )
        results.append(("emails (SendGrid)", "OK", True))
    except Exception as e:
        results.append(("emails (SendGrid)", str(e), False))

    try:
        from src.admin_dashboard import DASHBOARD_HTML
        results.append(("admin_dashboard", f"OK ({len(DASHBOARD_HTML):,} chars)", True))
    except Exception as e:
        results.append(("admin_dashboard", str(e), False))

    try:
        from src.main import main, create_parser
        results.append(("main (CLI)", "OK", True))
    except Exception as e:
        results.append(("main (CLI)", str(e), False))

    try:
        from src.metrics import MetricsRegistry, MetricsMiddleware, render_metrics
        results.append(("metrics (/metrics exporter)", "OK", True))
    except Exception as e:
        results.append(("metrics (/metrics exporter)", str(e), False))

    try:
        from src.alerts import send_alert
        results.append(("alerts (webhook)", "OK", True))
    except Exception as e:
        results.append(("alerts (webhook)", str(e), False))

    try:
        from src.backfill import backfill_history, build_backfill_history
        results.append(("backfill (Neon bulk load)", "OK", True))
    except Exception as e:
        results.append(("backfill (Neon bulk load)", str(e), False))

    # ── FastAPI API Layer ─────────────────────────────────────────────
    print("\n2. Checking API layer...")

    try:
        import fastapi
        results.append(("fastapi", f"v{fastapi.__version__}", True))
    except Exception as e:
        results.append(("fastapi", str(e), False))

    try:
        from src.api import (
            app,
            ScreenRequest,
            ScreenResponse,
            BacktestRequest,
            EnrichRequest,
            HealthResponse,
            ConfigStatus,
            StockResult,
            BacktestResultItem,
            EnrichResponse,
            RegisterRequest,
            LoginRequest,
            UserOut,
            AuthResponse,
            VerifyEmailRequest,
        )
        results.append(("api — models", "OK", True))
    except Exception as e:
        results.append(("api — models", str(e), False))

    try:
        from src.auth import (
            hash_password,
            verify_password,
            create_access_token,
            verify_access_token,
            get_current_user,
            require_admin,
        )
        results.append(("auth", "OK", True))
    except Exception as e:
        results.append(("auth", str(e), False))

    # ── Route Registration ────────────────────────────────────────────
    try:
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        expected_routes = [
            "/health",
            "/config",
            "/screen",
            "/interpret",
            "/backtest",
            "/backtest/export",
            "/enrich",
            "/stocks",
            "/stocks/{symbol}",
            "/refresh",
            "/news",
            "/news/{symbol}",
            "/auth/register",
            "/auth/login",
            "/auth/me",
            "/auth/verify-email",
            "/auth/resend-verification",
            "/admin",
            "/admin/overview",
            "/admin/users",
            "/admin/jobs",
            "/ops/jobs",
            "/ops/jobs/{job_name}",
            "/metrics",
            "/",
            "/meta",
            "/meta/sectors",
            "/meta/industries",
        ]
        missing = [r for r in expected_routes if r not in routes]
        if missing:
            results.append(("routes", f"missing: {missing}", False))
        else:
            results.append(("routes", f"{len(routes)} routes: {', '.join(sorted(routes))}", True))
    except Exception as e:
        results.append(("routes", str(e), False))

    # ── API Smoke Tests ───────────────────────────────────────────────
    print("\n3. Running API smoke tests (via TestClient)...")

    try:
        from fastapi.testclient import TestClient
        from tests.fakes import FakeLLMRouter

        # Hermetic: fake the LLM router so the suite never makes live Z.ai
        # calls (saves free-tier quota, keeps tests network-independent).
        mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

        client = TestClient(app)

        # Health
        resp = client.get("/health")
        got = resp.json()
        assert resp.status_code == 200
        assert got["status"] == "ok"
        results.append(("GET /health", f"200 — status=ok, keys={got['finnhub_keys_configured']}", True))

        # Config
        resp = client.get("/config")
        got = resp.json()
        assert resp.status_code == 200
        assert "warnings" in got
        results.append(("GET /config", f"200 — {len(got['warnings'])} warnings", True))

        # Interpret (POST with body)
        resp = client.post("/interpret", json={"query": "undervalued tech stocks"})
        got = resp.json()
        assert resp.status_code == 200
        assert "filters" in got
        results.append(("POST /interpret", f"200 — {len(got['filters'])} filters", True))

        # Screen (may fail if no stock data, but request itself should work)
        resp = client.post("/screen", json={"query": "tech stocks", "limit": 5})
        if resp.status_code == 200:
            results.append(("POST /screen", "200 — OK", True))
        else:
            # If PostgreSQL is not configured / empty, expect 502
            results.append(("POST /screen", f"{resp.status_code}", resp.status_code in (200, 502)))

        # Enrich
        resp = client.post("/enrich", json={"query": "tech stocks", "max_stocks": 3})
        if resp.status_code == 200:
            results.append(("POST /enrich", "200 — OK", True))
        else:
            results.append(("POST /enrich", f"{resp.status_code}", resp.status_code in (200, 502)))

        # Stocks list
        resp = client.get("/stocks?limit=3")
        if resp.status_code == 200:
            results.append(("GET /stocks", "200 — OK", True))
        else:
            results.append(("GET /stocks", f"{resp.status_code}", resp.status_code in (200, 503, 502)))

        # Backtest requires auth (401 without a token) — no paywall, free
        # for every authenticated user.
        resp = client.post("/backtest", json={"query": "tech stocks", "top_n": 2})
        assert resp.status_code == 401, f"backtest w/o auth: {resp.status_code}"
        results.append(("POST /backtest (no auth)", "401 — auth gate works", True))

        # 404 for unknown stock (or 503 when Postgres isn't configured on a
        # bare CI runner — the endpoint degrades to "not configured" before
        # it can resolve the symbol; both are correct responses).
        resp = client.get("/stocks/INVALID")
        assert resp.status_code in (404, 503), f"/stocks/INVALID: {resp.status_code}"
        if resp.status_code == 404:
            got = resp.json()
            assert got["error"] is True
        results.append(("GET /stocks/INVALID", f"{resp.status_code} — correct error response", True))

        # 422 for validation error (empty query)
        resp = client.post("/interpret", json={"query": ""})
        assert resp.status_code == 422
        results.append(("POST /interpret (empty)", "422 — validation works", True))

    except Exception as e:
        results.append(("smoke tests", f"FAILED: {e}", False))

    # ── Results ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    all_pass = True
    for name, detail, passed in results:
        icon = "✅" if passed else "❌"
        print(f"  {icon}  {name}: {detail}")
        if not passed:
            all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("✅  ALL CHECKS PASSED")
    else:
        print("❌  SOME CHECKS FAILED")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
