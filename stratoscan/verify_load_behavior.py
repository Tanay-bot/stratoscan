"""Load test for the Stratoscan API (Tier 2 ops gap #4).

Boots a REAL uvicorn server (the same app production runs) on a free port
with the rate limiter ON, then hammers it with concurrent requests and
asserts the three things a production API must not fail:

  1. **Latency**: p50 / p95 / p99 / max under modest concurrent load.
  2. **Rate limiting engages**: bursting past a per-IP cap returns 429
     (not 500, not silent success — the abuse shield must actually fire).
  3. **Zero 5xx**: no endpoint ever returns a server error under load.

Hermetic by design: no external network, no DB, no LLM keys — it only
exercises endpoints that don't need them (/health, /config, /metrics,
/docs, /openapi.json, /stocks). Run: python3 verify_load_behavior.py

Exit code 0 = all assertions passed.
"""

from __future__ import annotations

import concurrent.futures
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Hermetic: rate limiter ON (we're testing it), Redis OFF (in-memory store
# — one process, exact enough for the 429 assertions).
os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

# Latency targets EXCLUDE /health on purpose: it performs a live SELECT 1
# against the screener DB, and a suspended Neon cold start can take seconds
# — that's DB latency, not API latency, and it would dominate the p95. The
# readiness probe below still uses /health (once) to confirm the server is up.
BASE_TARGETS = ("/config", "/metrics", "/docs", "/openapi.json")

PASS = "✅"
FAIL = "❌"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not become ready")


def _request(port: int, path: str) -> tuple:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as r:
            latency = (time.perf_counter() - start) * 1000
            return r.status, latency
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - start) * 1000
        return e.code, latency


def main() -> int:
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ok = True
    try:
        print("=" * 64)
        print("Stratoscan — Load & Rate-Limit Behavior Probe")
        print("=" * 64)
        print(f"Booting uvicorn on 127.0.0.1:{port} (rate limiter ON)...")
        _wait_ready(port)
        print(f"{PASS} server ready")

        # Warm-up: first hits to each endpoint trigger lazy imports inside
        # the handlers; excluding them keeps the latency stats honest.
        for target in BASE_TARGETS:
            _request(port, target)

        # ── 1. Latency under modest concurrent load ─────────────────────
        # 3 workers × 5 rounds = 15 requests, deliberately under the read
        # scope's burst cap (60/min) so this phase never trips the limiter
        # (the burst phase below tests that separately).
        print("\n--- 1. Concurrent latency (3 workers × 5 rounds) ---")
        latencies: list = []
        statuses: dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futs = [
                ex.submit(_request, port, target)
                for _ in range(5)
                for target in BASE_TARGETS
            ]
            for fut in concurrent.futures.as_completed(futs):
                status, latency = fut.result()
                statuses[status] = statuses.get(status, 0) + 1
                latencies.append(latency)

        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0
        worst = max(latencies) if latencies else 0.0
        print(f"  requests={len(latencies)}  p50={p50:.1f}ms  p95={p95:.1f}ms  "
              f"p99={p99:.1f}ms  max={worst:.1f}ms")
        print(f"  status distribution: {statuses}")

        bad = [s for s in statuses if s >= 500]
        if bad:
            print(f"{FAIL} 5xx responses under load: {bad}")
            ok = False
        elif p95 < 2000:
            print(f"{PASS} zero 5xx + p95 {p95:.1f}ms < 2s")
        else:
            print(f"{FAIL} p95 {p95:.1f}ms exceeds 2s budget")
            ok = False

        # ── 2. /metrics is scrape-able and well-formed ──────────────────
        # Run BEFORE the burst phase so the read scope isn't exhausted yet
        # (a 429 here would be the limiter working, but it'd fail the
        # format check — we want to test the exporter, not the limiter).
        print("\n--- 2. /metrics Prometheus text format ---")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics", timeout=5
            ) as r:
                body = r.read().decode("utf-8")
                ctype = r.headers.get("Content-Type", "")
            checks = {
                "stratoscan_http_requests_total": "requests_total" in body,
                "stratoscan_http_request_duration_seconds": "duration_seconds" in body,
                "stratoscan_uptime_seconds": "uptime_seconds" in body,
                "text/plain content-type": "text/plain" in ctype,
            }
            for name, passed in checks.items():
                print(f"  {'✅' if passed else '❌'} {name}")
                if not passed:
                    ok = False
        except Exception as e:
            print(f"{FAIL} /metrics fetch failed: {e}")
            ok = False

        # ── 3. Rate limiter engages (per-IP burst) ─────────────────────
        # /stocks is a "read" scope: burst 60/min per IP. Firing 90 fast
        # must produce 429s — the abuse shield fires, not silent success.
        print("\n--- 3. Rate limiter engages (90 rapid /stocks reads) ---")
        burst_statuses: dict = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(_request, port, "/stocks?limit=1") for _ in range(90)]
            for fut in concurrent.futures.as_completed(futs):
                status, _ = fut.result()
                burst_statuses[status] = burst_statuses.get(status, 0) + 1
        print(f"  status distribution: {burst_statuses}")
        n429 = burst_statuses.get(429, 0)
        n200 = burst_statuses.get(200, 0)
        if n429 > 0 and n200 > 0 and all(s < 500 for s in burst_statuses):
            print(f"{PASS} limiter fired: {n429}×429 + {n200}×200, zero 5xx")
        else:
            print(f"{FAIL} expected 429s + 200s and no 5xx, got {burst_statuses}")
            ok = False

        # ── 4. Zero 5xx across the whole burst ─────────────────────────
        all_5xx = [s for s in list(statuses) + list(burst_statuses) if s >= 500]
        if all_5xx:
            print(f"\n{FAIL} 5xx observed: {all_5xx}")
            ok = False
        else:
            print(f"\n{PASS} zero 5xx across all {len(latencies) + 90} requests")

        print("\n" + "=" * 64)
        print("✅ LOAD TEST PASSED" if ok else "❌ LOAD TEST FAILED")
        print("=" * 64)
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
