"""Hermetic tests for the frontend-facing discovery / meta surface.

Covers the endpoints a frontend relies on to render itself WITHOUT
hardcoding the API surface:

  - GET /                  → service identity (name, version, docs, meta link)
  - GET /meta              → capability manifest: endpoint catalog with auth
                             classification, sortable columns, filter fields,
                             example queries, rate limits, pagination contract,
                             auth feature flags
  - GET /meta/sectors      → distinct sectors + live counts (dropdown data)
  - GET /meta/industries   → distinct industries + live counts (dropdown data)
  - is_admin               → exposed on UserOut so the frontend can gate the
                             admin UI (register/login/me carry it)

Hermetic: rate limiter off, LLM router faked, DB calls mocked where the
sector/industry endpoints hit Postgres.

Run: python -m tests.test_frontend_meta
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
# Pin the env so a local .env with APP_ENV=production can't flip this suite
# into the docs-disabled surface (docs links are asserted below).
os.environ["APP_ENV"] = "development"

from unittest import mock

from fastapi.testclient import TestClient
from src.api import app
from tests.fakes import FakeLLMRouter

mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

client = TestClient(app)


def _result(label: str, ok: bool) -> None:
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}")
    if not ok:
        raise AssertionError(label)


# ── GET / — service identity ─────────────────────────────────────────────────
r = client.get("/")
assert r.status_code == 200, f"root status {r.status_code}"
body = r.json()
assert body.get("name") == "Stratoscan API"
assert body.get("version")
assert body.get("meta") == "/meta"
assert isinstance(body.get("endpoint_count"), int) and body["endpoint_count"] > 20
_result("GET / returns service identity", True)

# ── GET /meta — capability manifest ──────────────────────────────────────────
r = client.get("/meta")
assert r.status_code == 200, f"meta status {r.status_code}"
m = r.json()

# Endpoint catalog is derived from the LIVE routes and covers all the major
# surfaces, each classified with its auth requirement.
endpoint_paths = [e["path"] for e in m["endpoints"]]
for required in ("/screen", "/backtest", "/enrich", "/news", "/stocks",
                 "/auth/register", "/auth/login", "/auth/me", "/admin/users",
                 "/admin/jobs", "/ops/jobs", "/refresh", "/interpret"):
    assert required in endpoint_paths, f"/meta catalog missing {required}"
by_path = {e["path"]: e for e in m["endpoints"]}
assert by_path["/backtest"]["auth"] == "bearer"
assert by_path["/admin/users"]["auth"] == "admin"
assert by_path["/admin/overview"]["auth"] == "admin"
assert by_path["/screen"]["auth"] == "none"
assert by_path["/auth/me"]["auth"] == "bearer"
# Day 6: trial routes take X-Anon-Token; claiming needs the account too;
# the public share page and the session mint need nothing.
assert by_path["/try/backtest"]["auth"] == "trial"
assert by_path["/results/share"]["auth"] == "trial"
assert by_path["/try/claim"]["auth"] == "bearer"
assert by_path["/try/session"]["auth"] == "none"
assert by_path["/r/{share_id}"]["auth"] == "none"
_result("GET /meta endpoint catalog complete + auth-classified", True)

# Sortable columns match what the screener actually accepts.
assert "market_cap" in m["sortable_columns"]
assert "score" in m["sortable_columns"]
assert len(m["sortable_columns"]) >= 20
_result(f"GET /meta sortable_columns ({len(m['sortable_columns'])})", True)# Filter fields carry operators + display metadata for a filter-builder UI.
ff = {f["field"]: f for f in m["filter_fields"]}
for f in ("sector", "market_cap", "pe_ratio", "rsi_14", "dividend_yield",
          "roe_ttm", "revenue_growth_ttm"):
    assert f in ff, f"filter field missing: {f}"
    assert ff[f]["operators"] and ff[f]["type"]
assert m["filter_fields"][0]["query_key"]
# Momentum + derived boolean criteria the interpreter supports must be
# exposed too (a filter-builder UI would silently miss them otherwise).
for f in ("momentum", "above_sma_50", "golden_cross", "near_52w_high",
          "macd_bullish", "bollinger_breakout", "bollinger_oversold"):
    assert f in ff, f"derived filter field missing: {f}"
    assert ff[f]["type"] in ("boolean", "string")
_result(f"GET /meta filter_fields ({len(m['filter_fields'])}) incl. momentum+derived", True)

# Example queries (frontend "try these" presets).
assert len(m["example_queries"]) >= 5
assert all(isinstance(q, str) and q for q in m["example_queries"])
# Day 14: /meta advertises nothing that does not work. Every example parses on
# the free path into real criteria, and none mentions data that doesn't exist.
from src.deterministic_interpreter import DeterministicInterpreter  # noqa: E402
from src.interpreter import DERIVED_CRITERIA_KEYS  # noqa: E402

for q in m["example_queries"]:
    parsed, conf = DeterministicInterpreter().parse(q)
    n = (len(parsed.filters) + bool(parsed.sector) + bool(parsed.momentum) + bool(parsed.asset_type)
         + sum(getattr(parsed, k) is not None for k in DERIVED_CRITERIA_KEYS))
    assert conf >= 0.6 and n >= 2, (q, conf, n)
    assert "debt" not in q.lower(), q  # no debt data exists
assert any("etf" in q.lower() for q in m["example_queries"])
assert ff["asset_type"]["query_key"] == "asset_type"
_result(f"GET /meta example_queries ({len(m['example_queries'])}) all parse into real criteria", True)

# Rate-limit budgets the frontend can surface to the user.
for scope in ("screen", "backtest", "enrich", "read", "auth"):
    assert scope in m["rate_limits"], f"rate_limits missing {scope}"
    assert m["rate_limits"][scope]["daily"] > 0
_result(f"GET /meta rate_limits ({len(m['rate_limits'])} scopes)", True)

# Pagination contract.
assert m["pagination"]["default_limit"] == 50
assert m["pagination"]["max_limit"] >= 100
assert "next_offset" in m["pagination"]["fields"]
_result("GET /meta pagination contract", True)

# Auth feature flags.
assert "jwt_expire_hours" in m["auth"]
assert m["auth"]["password_min_length"] >= 8
assert "email_verification_required" in m["auth"]
_result("GET /meta auth flags", True)

# Docs links.
assert m["docs"]["swagger"] == "/docs"
assert m["docs"]["openapi"].endswith("openapi.json")
_result("GET /meta docs links", True)

# ── GET /meta/sectors + /meta/industries (dropdown reference data) ───────────
# DB-backed: the endpoint does ``from src.database import PostgresDB`` at call
# time, so patching src.database.PostgresDB with a fake class is hermetic.
class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._rows)


class _FakeDB:
    def __init__(self):
        self._rows = [("Technology", 1902), ("Finance", 1693), ("Healthcare", 1200)]

    def _get_conn(self):
        return _FakeConn(self._rows)


# Hermetic: the /meta/sectors endpoint checks settings.has_database_url
# BEFORE touching PostgresDB (which is patched below with _FakeDB). On a
# clean CI runner with no .env that guard 503s first — point database_url
# at a dummy so the guard passes and the fake serves the rows. The
# unconfigured-503 path is asserted separately by patching the property.
from src.config import settings as meta_settings  # noqa: E402
meta_settings.database_url = "postgresql://ci-dummy-stratoscan"

with mock.patch("src.database.PostgresDB", _FakeDB):
    r = client.get("/meta/sectors")
    assert r.status_code == 200, f"sectors status {r.status_code}"
    s = r.json()
    assert s["total"] == 3
    assert s["values"][0] == {"name": "Technology", "count": 1902}
    _result("GET /meta/sectors returns distinct sectors + counts", True)

    r = client.get("/meta/industries")
    assert r.status_code == 200, f"industries status {r.status_code}"
    i = r.json()
    assert i["total"] == 3
    assert i["values"][0]["name"] == "Technology"
    _result("GET /meta/industries returns distinct industries + counts", True)

# /meta/sectors when the DB is NOT configured → clean 503, not a 500.
# has_database_url is a read-only Settings property, so patch it at the
# class level (mock.patch.object on an instance property can't restore it).
from src.api import settings as api_settings  # noqa: E402

with mock.patch.object(
    type(api_settings), "has_database_url", property(lambda self: False)
):
    r = client.get("/meta/sectors")
    assert r.status_code == 503, f"sectors (no DB) status {r.status_code}"
    _result("GET /meta/sectors returns 503 when DB unconfigured", True)

# ── is_admin exposed on the auth surface ─────────────────────────────────────
spec = client.get("/openapi.json").json()
user_out = spec["components"]["schemas"]["UserOut"]["properties"]
assert "is_admin" in user_out, "UserOut missing is_admin"
auth_resp = spec["components"]["schemas"]["AuthResponse"]["properties"]["user"]
assert "is_admin" in spec["components"]["schemas"]["UserOut"]["properties"]
_result("UserOut schema exposes is_admin", True)

# /auth/me returns is_admin for the authenticated user (fake token via
# directly calling the dependency is covered by test_auth; here we just
# assert the endpoint 401s without a token — the schema carries the field).
r = client.get("/auth/me")
assert r.status_code == 401
_result("GET /auth/me requires auth (401 without token)", True)

print("\n✅ ALL FRONTEND META TESTS PASSED")
