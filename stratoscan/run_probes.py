#!/usr/bin/env python3
"""Release-gate probe runner (P0-12) — runs all 18 ``verify_*.py`` behavior
probes against the isolated ``stratoscan_probes`` database.

Every probe runs in a subprocess whose environment is staged FIRST:
  1. ``staging.env`` is loaded (override=True) so ALL settings — LLM keys,
     Finnhub keys, CORS, timeouts — resolve to staging resources.
  2. ``DATABASE_URL`` is then forced to ``STAGING_DATABASE_URL`` (the probes
     DB). Any probe that boots a server or opens ``src.config`` therefore
     touches ONLY ``stratoscan_probes`` — never production, never the E2E
     staging DB. Probes that use fakes/SQLite ignore it entirely.
  3. ``verify_throughput`` gets its own freshly-booted uvicorn (rate limiter
     ON, Redis OFF -> in-memory store) so its 429/header assertions are
     meaningful and isolated from the always-on staging API server.

Exit code: 0 if every probe passed (or cleanly skipped), 1 otherwise.

Isolation note: every probe runs with DATABASE_URL forced to the probes DB
and Upstash creds blanked. The ONE intentional exception is
verify_backtest_neon_lifecycle.py, which drives the shared Neon BACKTEST
DB (staging reuses prod's backtest project) — it is self-cleaning by design
(probe-only symbols PRBE/PRBENCH, snapshot-and-restore counts), so no real
backtest data is at risk.

Run: /usr/local/bin/python3.14 run_probes.py [--only NAME ...] [--report out.md]

Note: the probe count in RELEASE_GATE.md and this docstring must stay in
sync with the PROBES list below.

CI mode: when ``staging.env`` is absent (e.g. GitHub Actions), settings are
read from the process environment instead — set the same vars as secrets
(STAGING_DATABASE_URL, SCREENER_LLM_KEY, ...) and skip the local file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

PROBES = [
    "verify_audit_behavior.py",
    "verify_backtest_accuracy.py",
    "verify_backtest_edge_paths.py",
    "verify_backtest_neon_lifecycle.py",
    "verify_data_accuracy.py",
    "verify_derived_fix.py",
    "verify_failure_drills.py",
    "verify_full_backend_behavior.py",
    "verify_llm_router_lifecycle.py",
    "verify_load_behavior.py",
    "verify_screen_cache_live.py",
    "verify_screener_lifecycle.py",
    "verify_scheduler_watchdog.py",
    "verify_security_findings.py",
    "verify_security_fixes_behavior.py",
    "verify_throughput.py",
    "verify_trade_pnl.py",
    "verify_upstash_ratelimit.py",
]

PROBE_PASS = "✅"
PROBE_FAIL = "❌"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except urllib.error.HTTPError:
            return True  # any HTTP response proves uvicorn is bound
        except Exception:
            time.sleep(0.4)
    return False


# The throughput probe's QUERIES — pre-warmed into the interpret cache so a
# slow COLD LLM interpret (staging ZAI can take 15-40s) never trips the
# probe's 20s client timeout. Real interpret still happens; it just happens
# here with a generous timeout, once per query.
WARM_QUERIES = [
    "large cap tech stocks with strong revenue growth",
    "undervalued dividend stocks under $50",
    "high momentum growth stocks in healthcare",
    "small cap stocks with low debt and rising earnings",
    "banks with high profit margins and stable prices",
]


def _warm_interpret_cache(port: int) -> None:
    """POST each throughput query once so its interpret lands in the cache.

    Warm via /interpret (bucket burst 10/min) NOT /screen (burst 5/min):
    the interpret cache is keyed on the raw query and shared by both
    endpoints, so /screen calls afterwards hit the warm cache without
    burning the tiny /screen burst budget."""
    for q in WARM_QUERIES:
        body = json.dumps({"query": q}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/interpret", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                r.read()
            print(f"    warm ✓ '{q[:44]}'")
        except Exception as e:  # noqa: BLE001
            print(f"    warm ✗ '{q[:44]}' ({type(e).__name__}: {str(e)[:80]})")


def _check_port_free(port: int, who: str) -> bool:
    """verify_screen_cache_live hardcodes 8099; a squatter would make it
    silently test the wrong server (its logs never appear in the probe's
    captured file). Refuse to run instead of mis-testing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        print(f"❌ Port {port} is occupied — {who} hardcodes it and would test "
              f"the wrong server. Free it (kill the squatter) and re-run.")
        return False


def _build_env() -> dict:
    """Resolve the probe environment, in order:

    1. If ``staging.env`` exists (local dev), load it with override so ALL
       settings resolve to staging resources.
    2. Otherwise (CI: GitHub Actions), trust the process environment — the
       caller injected the same vars as secrets.

    Either way DATABASE_URL is then forced to the probes DB (``STAGING_DATABASE_URL``),
    Upstash creds are blanked, and PYTHONPATH is set so probes can import src.
    """
    from dotenv import load_dotenv

    env_path = os.path.join(HERE, "staging.env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)

    probes_url = os.environ.get("STAGING_DATABASE_URL", "")
    if not probes_url:
        print("❌ STAGING_DATABASE_URL not set (staging.env or environment). "
              "Run seed_probes_db.py first, or export it.")
        sys.exit(1)
    if "stratoscan_probes" not in probes_url:
        print(f"❌ STAGING_DATABASE_URL must point at stratoscan_probes, got: {probes_url[:60]}...")
        sys.exit(1)

    env = os.environ.copy()
    env["DATABASE_URL"] = probes_url
    env["PYTHONPATH"] = HERE + os.pathsep + env.get("PYTHONPATH", "")
    # Keep the probes isolated: never let a probe reach Upstash/Redis.
    env["REDIS_RATE_LIMIT_ENABLED"] = "false"
    env["UPSTASH_REDIS_REST_URL"] = ""
    env["UPSTASH_REDIS_REST_TOKEN"] = ""
    return env


def _run_one(script: str, env: dict, extra_args: list[str] | None = None) -> tuple[int, str]:
    """Run one probe, returning (exit_code, tail_of_output)."""
    cmd = [PY, script] + (extra_args or [])
    try:
        proc = subprocess.run(
            cmd, cwd=HERE, env=env, capture_output=True, text=True, timeout=600
        )
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT after 600s"
    tail = "\n".join(out.strip().splitlines()[-12:])
    return proc.returncode, tail


def _render_report(results: list, report_path: str, started: str) -> None:
    """Write a self-contained markdown pass/fail report."""
    passed = [r for r in results if r[1] == 0]
    failed = [r for r in results if r[1] != 0]
    lines = [
        "# Stratoscan — Release Gate Report",
        "",
        f"- **Ran:** {started}",
        "- **Environment:** staging (isolated `stratoscan_probes` DB)",
        "- **Result:** " + ("✅ PASS" if not failed else f"❌ FAIL ({len(failed)}/{len(results)})"),
        f"- **Probes:** {len(passed)}/{len(results)} passed",
        "",
        "| Probe | Exit |",
        "|---|---|",
    ]
    for script, code, tail in results:
        lines.append(f"| {script} | {code} |")
    if failed:
        lines += ["", "## Failures", ""]
        for script, code, tail in failed:
            # Escape backticks so a probe's own output can never break the
            # markdown code fence (e.g. a probe printing a nested ``` block).
            safe_tail = tail.replace("`", "\\`")
            lines += [f"### {script} (exit {code})", "", "```", safe_tail, "```", ""]
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"📄 Report written to {report_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the 15 behavior probes against staging.")
    ap.add_argument("--only", nargs="*", help="Only run these probe file names")
    ap.add_argument("--report", metavar="PATH",
                    help="Write a markdown pass/fail report to this path")
    args = ap.parse_args()

    env = _build_env()
    probes = args.only or PROBES
    started = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Guard: verify_screen_cache_live hardcodes 8099 — never silently
    # mis-test a squatter.
    if "verify_screen_cache_live.py" in probes and not _check_port_free(8099, "verify_screen_cache_live.py"):
        return 1

    # Boot a dedicated rate-limited uvicorn for verify_throughput.
    tp_env = env.copy()
    tp_env["RATE_LIMIT_ENABLED"] = "true"  # throughput probe asserts 429s + headers
    tp_port = _free_port()
    tp_proc: subprocess.Popen | None = None

    results: list[tuple[str, int, str]] = []
    try:
        if "verify_throughput.py" in probes:
            print(f"🚀 Booting probe server on 127.0.0.1:{tp_port} (rate limiter ON, in-memory)...")
            tp_log = open("/tmp/stratoscan_probe_server.log", "w")
            tp_proc = subprocess.Popen(
                [PY, "probe_server_entry.py", "--host", "127.0.0.1",
                 "--port", str(tp_port), "--log-level", "warning"],
                cwd=HERE, env=tp_env, stdout=tp_log, stderr=subprocess.STDOUT, text=True,
            )
            if not _wait_ready(tp_port):
                results.append(("verify_throughput.py", -1, "probe server failed to start"))
                probes = [p for p in probes if p != "verify_throughput.py"]
            else:
                print("🚀 Pre-warming the interpret cache (cold LLM interprets are slow)...")
                _warm_interpret_cache(tp_port)

        for script in probes:
            if script == "verify_throughput.py":
                # N_UNIQUE=2: 2 queries x 2 calls = 4 /screen calls, under the
                # burst-5/min bucket (3 queries would 429 on the 6th call).
                code, tail = _run_one(script, tp_env, [f"http://127.0.0.1:{tp_port}", "2"])
            else:
                code, tail = _run_one(script, env)
            results.append((script, code, tail))

    finally:
        if tp_proc is not None:
            tp_proc.terminate()
            try:
                tp_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tp_proc.kill()
        try:
            tp_log.close()
        except (NameError, UnboundLocalError, OSError):
            pass

    # ── Report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("PROBE RESULTS — isolated stratoscan_probes DB")
    print("=" * 72)
    failed = []
    for script, code, tail in results:
        ok = code == 0
        marker = PROBE_PASS if ok else PROBE_FAIL
        print(f"  {marker} {script:<38} exit={code}")
        if not ok:
            failed.append(script)
            print("      └─ " + tail.replace("\n", "\n         "))
    print("=" * 72)
    if args.report:
        _render_report(results, args.report, started)
    if failed:
        print(f"❌ {len(failed)} probe(s) FAILED: {failed}")
        return 1
    print(f"✅ All {len(results)} probes passed against stratoscan_probes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
