"""Hermetic tests for P0-10: production docs gating + env-driven CORS.

Locks in two production-safety behaviors:

  1. APP_ENV=production must kill /docs, /redoc, and /openapi.json (404) —
     the interactive docs expose the full route/model surface to anyone.
  2. CORS_ALLOW_ORIGINS is read from the environment: a preflight from a
     configured origin returns 200 and echoes that exact origin (with
     credentials), while an unlisted origin is NEVER echoed.

Also asserts the dev regression: APP_ENV=development still serves /docs and
/openapi.json (this is verified in a fresh subprocess so the two modes never
share interpreter state).

Hermetic: rate limiter off, Redis off, LLM router faked, no DB, no network.

Run: python -m tests.test_docs_gating
"""

import os
import subprocess
import sys
import textwrap

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# ── Production-mode import (BEFORE importing src.api) ───────────────────────
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ["APP_ENV"] = "production"
os.environ["CORS_ALLOW_ORIGINS"] = "https://app.stratoscan.com"

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.api import app  # noqa: E402
from src.config import settings  # noqa: E402
from tests.fakes import FakeLLMRouter  # noqa: E402

mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

client = TestClient(app)


def _result(label: str, ok: bool) -> None:
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}")
    if not ok:
        raise AssertionError(label)


# ── 1. Docs gating: production kills the interactive surface ────────────────
assert settings.is_production, "settings.is_production should be True"
_result("APP_ENV=production -> settings.is_production True", True)

# Structural pin (root cause): the FastAPI() constructor args are None in
# production — not just the 404 symptom.
assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None
_result("app.docs_url/redoc_url/openapi_url are None in production", True)

for path in ("/docs", "/redoc", "/openapi.json"):
    r = client.get(path)
    assert r.status_code == 404, f"{path} -> {r.status_code} in production"
    _result(f"GET {path} -> 404 in production", True)

# The service still answers (only the docs surface is gone).
r = client.get("/")
assert r.status_code == 200, f"root status {r.status_code}"
_result("GET / still 200 in production", True)

# /meta must NOT advertise docs URLs when they don't exist (api.py returns
# an EMPTY dict in production — no swagger/redoc/openapi keys at all).
r = client.get("/meta")
assert r.status_code == 200, f"meta status {r.status_code}"
docs = r.json()["docs"]
assert docs == {}, f"/meta advertises docs in production: {docs!r}"
_result("GET /meta advertises no docs in production", True)

# ── 2. CORS is env-driven and exact ─────────────────────────────────────────
assert "https://app.stratoscan.com" in settings.cors_origins
_result("CORS_ALLOW_ORIGINS read from env (cors_origins parsed)", True)

# Preflight from a configured origin -> 200 + exact origin + credentials.
r = client.options(
    "/screen",
    headers={
        "Origin": "https://app.stratoscan.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,authorization",
    },
)
assert r.status_code == 200, f"preflight status {r.status_code}"
assert r.headers.get("access-control-allow-origin") == "https://app.stratoscan.com", \
    f"wrong allow-origin: {r.headers.get('access-control-allow-origin')!r}"
assert r.headers.get("access-control-allow-credentials") == "true"
_result("CORS preflight from configured origin -> 200 + exact origin + credentials", True)

# Unlisted origin must NEVER be echoed.
r = client.options(
    "/screen",
    headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "POST",
    },
)
allow = r.headers.get("access-control-allow-origin", "")
assert allow != "https://evil.example.com", "disallowed origin leaked!"
_result("CORS disallowed origin not echoed", True)

# ── 3. Dev regression (fresh interpreter, no shared state) ──────────────────
DEV_SCRIPT = textwrap.dedent(
    """
    import os, sys
    ROOT = "__ROOT__"
    sys.path.insert(0, ROOT)
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
    os.environ["APP_ENV"] = "development"
    from unittest import mock
    from fastapi.testclient import TestClient
    from src.api import app
    from tests.fakes import FakeLLMRouter
    mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
    c = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} in dev"
    print("DEV_OK")
    """
)
proc = subprocess.run(
    [sys.executable, "-c", DEV_SCRIPT.replace("__ROOT__", _PROJECT_ROOT)],
    cwd=_PROJECT_ROOT,
    capture_output=True,
    text=True,
    timeout=120,
)
assert proc.returncode == 0, f"dev regression failed: {proc.stdout}{proc.stderr}"
assert "DEV_OK" in proc.stdout
_result("Dev regression: /docs + /openapi.json 200 in subprocess", True)

print("\n✅ ALL DOCS-GATING + CORS TESTS PASSED")
