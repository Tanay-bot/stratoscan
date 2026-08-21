"""Phase 1 evidence probe — reproduce the three security findings behaviorally.

Drives the REAL FastAPI app (src.api:app) via TestClient and captures
what actually happens when an unauthenticated attacker exercises each
finding:

  [1] POST /refresh   — unauthenticated trigger of a ~5,000-Finnhub-call
                        background job (VULN-001). Expect: reachable with
                        NO auth (no 401/403) unless the finding is fixed.
  [2] Error disclosure — send a request that forces an internal exception
                        and inspect the JSON `detail`: does it contain raw
                        exception text (VULN-002)? Tested on /screen with
                        a DB that raises, and via the generic 500 handler.
  [3] GET /ops/jobs   — unauthenticated internal job telemetry incl. error
                        columns (VULN-003). Expect: reachable with no auth.

Run: python verify_security_findings.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.api import app  # noqa: E402

print("=" * 70)
print("SECURITY FINDINGS — behavioral verification (Phase 1 evidence)")
print("=" * 70)

client = TestClient(app)

# ═══════════════════════════════════════════════════════════════════════════════
# [1] POST /refresh — unauthenticated?
# ═══════════════════════════════════════════════════════════════════════════════
print("\n[1] POST /refresh (unauthenticated)...")

# Avoid actually firing 5,000 Finnhub calls: patch the job body so the
# request completes without network. What we're probing is the AUTH GATE,
# which is decided before/independent of the job body.
with mock.patch(
    "src.api.BackgroundTasks.add_task",  # swallow the background job
) as _add_task:
    resp = client.post("/refresh")
print(f"    status={resp.status_code}")
if resp.status_code in (401, 403):
    print("    ✅ BLOCKED without auth (finding fixed)")
else:
    body = resp.json()
    print(f"    ❌ REACHABLE without auth — body={body!r}")
    if _add_task.call_args:
        print("    ❌ background job (thousands of Finnhub calls) was scheduled")
    else:
        print("    (background task not scheduled this run)")

# ═══════════════════════════════════════════════════════════════════════════════
# [2] Error disclosure — raw exception text in 500/502 detail
# ═══════════════════════════════════════════════════════════════════════════════
print("\n[2] Error disclosure (raw exception in detail)...")

# 2a. Generic 500 handler: make an endpoint raise a distinctive internal
# exception and read what comes back.
print("  (2a) generic 500 handler with a distinctive internal error...")
with mock.patch("src.api._run_screen", side_effect=RuntimeError("SECRET_INTERNAL_MARKER_XYZ")):
    resp = client.post("/screen", json={"query": "undervalued tech stocks"})
print(f"    status={resp.status_code}, detail={resp.json().get('detail')!r}")
if "SECRET_INTERNAL_MARKER_XYZ" in resp.json().get("detail", ""):
    print("    ❌ RAW internal exception text leaked to the client")
else:
    print("    ✅ generic message returned")

# 2b. Endpoint-level 502: the /screen endpoint itself interpolates {e!s}.
print("  (2b) endpoint-level 502 ({e!s} interpolation)...")
with mock.patch(
    "src.api._run_screen",
    side_effect=ValueError("SCREENING_INTERNAL_LEAK_777"),
):
    # _run_screen raising ValueError is caught by the endpoint's try/except
    resp = client.post("/screen", json={"query": "test leak"})
print(f"    status={resp.status_code}, detail={resp.json().get('detail')!r}")
if "SCREENING_INTERNAL_LEAK_777" in resp.json().get("detail", ""):
    print("    ❌ endpoint 502 detail contains raw exception text")
else:
    print("    ✅ generic message returned")

# ═══════════════════════════════════════════════════════════════════════════════
# [3] GET /ops/jobs — unauthenticated internal telemetry
# ═══════════════════════════════════════════════════════════════════════════════
print("\n[3] GET /ops/jobs (unauthenticated)...")
resp = client.get("/ops/jobs")
print(f"    status={resp.status_code}")
if resp.status_code in (401, 403):
    print("    ✅ BLOCKED without auth (finding fixed)")
else:
    try:
        body = resp.json()
        keys = list(body.keys()) if isinstance(body, dict) else type(body).__name__
        print(f"    ❌ REACHABLE without auth — response keys={keys}")
    except Exception:
        print(f"    ❌ REACHABLE without auth — status={resp.status_code}")

print("\n" + "=" * 70)
print("EVIDENCE CAPTURE COMPLETE")
print("=" * 70)
