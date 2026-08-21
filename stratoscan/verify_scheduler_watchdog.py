#!/usr/bin/env python3
"""verify_scheduler_watchdog.py — LIVE watchdog behavior probe (P0-6).

Proves the scheduler watchdog end to end over real HTTP:

  1. Boots a real webhook sink (127.0.0.1 ephemeral port) that captures
     every POST body, exactly like a Slack/Discord/ntfy incoming webhook.
  2. Builds a REAL DataRefreshScheduler + REAL JobMonitor + REAL
     InMemoryJobMonitorStore (fake pool/db so no Finnhub/Neon calls).
  3. HEALTHY: all jobs fresh + fresh heartbeat -> _alert_if_stale() fires
     nothing; the sink stays empty.
  4. CRASH: heartbeat aged past the 45-min liveness rule (a dead scheduler)
     -> _alert_if_stale() fires; asserts the REAL "Scheduler health alert"
     POST arrives at the sink with level=error and "alive: False".
  5. RECOVERY: fresh heartbeat restored (a restarted scheduler) ->
     _alert_if_stale() fires nothing; sink count unchanged.
  6. FAILED JOB: a monitored run that raises fires the REAL
     "Background job failed: <job>" POST with the error surfaced.

Exit 0 = the watchdog works end to end. Run standalone or via run_probes.py
(release gate). No external network: the sink is loopback-only.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ── Webhook sink ─────────────────────────────────────────────────────────────
_RECEIVED: list[dict] = []
_RECEIVED_LOCK = threading.Lock()


class _Sink(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {"_raw": body.decode("utf-8", "replace")}
        with _RECEIVED_LOCK:
            _RECEIVED.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):  # silence request logging
        pass


def _start_sink() -> str:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/"


# CRITICAL ORDER: start the sink AND set the webhook env BEFORE importing any
# src module. src.config's settings singleton reads ALERT_WEBHOOK_URL at
# import time (pydantic-settings); setting it later would leave
# alert_webhook_url="" and send_alert() would silently no-op.
WEBHOOK = _start_sink()
os.environ["ALERT_WEBHOOK_URL"] = WEBHOOK

# ── Probe helpers (imports AFTER env setup) ─────────────────────────────────
from src.job_monitor import (  # noqa: E402
    InMemoryJobMonitorStore,
    JobMonitor,
    JOB_DEFINITIONS,
)
from src.scheduler import DataRefreshScheduler  # noqa: E402

NOW = time.time()

results = []
ok = True


def check(name, cond, detail=""):
    global ok
    results.append((name, cond, detail))
    if not cond:
        ok = False


def _wait_for_posts(n: int, timeout: float = 5.0) -> list[dict]:
    """Wait until at least ``n`` posts have arrived; return the captured list."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _RECEIVED_LOCK:
            if len(_RECEIVED) >= n:
                return list(_RECEIVED)
        time.sleep(0.05)
    with _RECEIVED_LOCK:
        return list(_RECEIVED)


def _make_scheduler(monitor=None):
    return DataRefreshScheduler(
        pool=mock.Mock(),
        db=mock.Mock(),
        news_db=mock.Mock(),
        monitor=monitor or JobMonitor(InMemoryJobMonitorStore()),
    )


def _seed_all_fresh(monitor):
    for job in [n for n, d in JOB_DEFINITIONS.items() if d.get("interval_seconds")]:
        with monitor.run(job) as run:
            run.records = 10
            run.details = "seeded"
        rows = [r for r in monitor._store._runs if r["job_name"] == job]
        rows[-1]["started_at"] = NOW - 120
        rows[-1]["finished_at"] = NOW - 60


def _fresh_heartbeat(monitor):
    monitor._store.record_heartbeat()
    monitor._store._heartbeat["last_beat_at"] = NOW - 60


def _age_heartbeat(monitor, age_s=46 * 60):
    monitor._store.record_heartbeat()
    monitor._store._heartbeat["last_beat_at"] = time.time() - age_s


def main() -> int:
    print(f"🚀 Webhook sink listening on {WEBHOOK}")

    # ── 1. Healthy -> silent ───────────────────────────────────────────
    print("\n1. HEALTHY scheduler -> no alert")
    with _RECEIVED_LOCK:
        _RECEIVED.clear()
    m = JobMonitor(InMemoryJobMonitorStore())
    _seed_all_fresh(m)
    _fresh_heartbeat(m)
    s = _make_scheduler(m)
    s._alert_if_stale()
    time.sleep(0.3)  # give any stray thread a chance (there should be none)
    with _RECEIVED_LOCK:
        healthy_posts = len(_RECEIVED)
    check("healthy: sink empty", healthy_posts == 0, f"{healthy_posts} posts")

    # ── 2. Crash -> alert fires over real HTTP ─────────────────────────
    print("\n2. CRASH (heartbeat aged past 45-min liveness) -> alert")
    with _RECEIVED_LOCK:
        _RECEIVED.clear()
    _age_heartbeat(m)          # simulate the scheduler dying
    s._alert_if_stale()        # the loop's 10-min health check
    posts = _wait_for_posts(1)
    check("crash: alert POST arrived", len(posts) >= 1, f"{len(posts)} posts")
    if posts:
        p = posts[0]
        text = p.get("text", "")
        check("crash: title 'Scheduler health alert'", "Scheduler health alert" in text, text[:120])
        check("crash: level error", p.get("level") == "error", str(p.get("level")))
        check("crash: reports scheduler not alive", "alive: False" in text, text[:160])

    # ── 3. Recovery -> silent again ────────────────────────────────────
    print("\n3. RECOVERY (fresh heartbeat = restarted scheduler) -> no alert")
    with _RECEIVED_LOCK:
        count_before = len(_RECEIVED)
    # Clear the alert throttle so "silent" can't be explained by throttling.
    from src import alerts as _alerts_mod
    _alerts_mod._last_sent.clear()
    _fresh_heartbeat(m)
    s._alert_if_stale()
    time.sleep(0.3)
    with _RECEIVED_LOCK:
        count_after = len(_RECEIVED)
    check("recovery: no new alert", count_after == count_before,
          f"{count_before} -> {count_after}")

    # ── 4. Failed job -> job-failure alert over real HTTP ──────────────
    print("\n4. FAILED JOB -> 'Background job failed' alert")
    with _RECEIVED_LOCK:
        _RECEIVED.clear()
    _alerts_mod._last_sent.clear()
    m2 = JobMonitor(InMemoryJobMonitorStore())
    try:
        with m2.run("price_refresh") as run:
            run.details = "quotes only"
            raise RuntimeError("finnhub down")
    except RuntimeError:
        pass
    posts = _wait_for_posts(1)
    check("failed job: alert POST arrived", len(posts) >= 1, f"{len(posts)} posts")
    if posts:
        p = posts[0]
        text = p.get("text", "")
        check("failed job: title names job",
              "Background job failed" in text and "price_refresh" in text, text[:120])
        check("failed job: error surfaced", "finnhub down" in text, text[:160])
        check("failed job: level error", p.get("level") == "error")

    # ── Results ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, cond, detail in results:
        icon = "✅" if cond else "❌"
        print(f"  {icon}  {name}: {detail}")
    print("\n" + "=" * 60)
    if ok:
        print("✅  WATCHDOG PROBE PASSED (healthy silent / crash alerts / recovery silent / failed-job alerts)")
    else:
        print("❌  WATCHDOG PROBE FAILED")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
