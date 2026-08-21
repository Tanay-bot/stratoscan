"""Hermetic tests for Tier 2 observability: /metrics exporter + alerting.

Run: python -m tests.test_metrics_alerts

Covers:
  1. MetricsRegistry: counters, histogram buckets, uptime, render format.
  2. render_metrics: full /metrics payload is well-formed text/plain.
  3. Alerts: no webhook configured → no-op; configured → dispatched
     (throttled per key); throttle suppresses repeat alerts within the
     interval; failure degrades to a warning, never a raise.
  4. MetricsMiddleware route-label collapsing (no per-id label explosion).

No network, no DB — everything is faked or in-memory.
"""

from __future__ import annotations

import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: no real webhook, no real email channel, no real DB.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ["ALERT_WEBHOOK_URL"] = ""
os.environ["ALERT_EMAIL"] = ""
os.environ["SENDGRID_API_KEY"] = ""
os.environ["EMAIL_FROM_ADDRESS"] = ""


def _reset_registry():
    from src.metrics import registry

    registry._requests.clear()
    registry._latency_sum.clear()
    registry._latency_count.clear()
    registry._bucket_hits.clear()


def main() -> int:
    from src import metrics as metrics_mod
    from src import alerts as alerts_mod
    from src.metrics import MetricsRegistry, MetricsMiddleware

    results = []
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        results.append((name, cond, detail))
        if not cond:
            ok = False

    # ── 1. MetricsRegistry ─────────────────────────────────────────────
    print("\n1. MetricsRegistry")
    r = MetricsRegistry()
    r.record_request("GET", "/health", 200, 0.005)
    r.record_request("GET", "/health", 200, 0.02)
    r.record_request("GET", "/stocks/{symbol}", 404, 1.2)
    body = r.render()
    check("requests_total counts", 'stratoscan_http_requests_total{method="GET",route="/health",status="200"} 2' in body, body[:120])
    check("histogram bucket le=0.01", 'stratoscan_http_request_duration_seconds_bucket{method="GET",route="/health",le="0.01"} 1' in body)
    check("histogram le=+Inf", 'le="inf"}' in body)
    check("uptime gauge present", "stratoscan_uptime_seconds" in body)

    # ── 2. render_metrics (full /metrics payload) ──────────────────────
    print("\n2. render_metrics")
    _reset_registry()
    metrics_mod.registry.record_request("GET", "/metrics", 200, 0.001)
    full = metrics_mod.render_metrics()
    check("has HELP lines", "# HELP" in full)
    check("has TYPE lines", "# TYPE stratoscan_http_requests_total counter" in full)
    check("job gauges attempted (no crash w/o DB)", isinstance(full, str))
    check("trailing newline", full.endswith("\n"))

    # ── 3. Alerts ──────────────────────────────────────────────────────
    print("\n3. Alerts")
    # 3a: neither channel configured → no-op (webhook AND email both blank)
    alerts_mod._last_sent.clear()
    with mock.patch.object(metrics_mod.settings, "alert_webhook_url", ""), \
         mock.patch.object(metrics_mod.settings, "alert_email", ""):
        sent = alerts_mod.send_alert("x", "y")
    check("neither channel → no-op False", sent is False)

    # 3b: configured → dispatched once, throttled for the repeat
    alerts_mod._last_sent.clear()
    with mock.patch.object(
        alerts_mod.settings, "alert_webhook_url", "https://hooks.example.com/x"
    ), mock.patch.object(
        alerts_mod.settings, "alert_webhook_min_interval_seconds", 600
    ), mock.patch.object(alerts_mod, "_dispatch", return_value=None) as dispatch:
        first = alerts_mod.send_alert("job failed", "err", throttle_key="job:price_refresh")
        second = alerts_mod.send_alert("job failed", "err", throttle_key="job:price_refresh")
        check("first dispatch True", first is True)
        check("throttled repeat False", second is False)
        dispatch.assert_called_once()

    # 3c: different keys are NOT throttled against each other
    alerts_mod._last_sent.clear()
    with mock.patch.object(
        alerts_mod.settings, "alert_webhook_url", "https://hooks.example.com/x"
    ), mock.patch.object(
        alerts_mod.settings, "alert_webhook_min_interval_seconds", 600
    ), mock.patch.object(alerts_mod, "_dispatch", return_value=None) as dispatch:
        alerts_mod.send_alert("a", throttle_key="job:a")
        alerts_mod.send_alert("b", throttle_key="job:b")
        check("different keys not throttled", dispatch.call_count == 2)

    # 3d: dispatch failure degrades to warning, send_alert still True
    alerts_mod._last_sent.clear()
    with mock.patch.object(
        alerts_mod.settings, "alert_webhook_url", "https://hooks.example.com/x"
    ), mock.patch.object(
        alerts_mod.settings, "alert_webhook_min_interval_seconds", 0
    ), mock.patch.object(alerts_mod, "_dispatch", side_effect=RuntimeError("boom")):
        sent = alerts_mod.send_alert("will fail", "x", throttle_key="job:fail")
        check("dispatch failure doesn't raise", sent is True)
        time.sleep(0.05)  # let the daemon thread run its failing dispatch

    # 3e: EMAIL channel — webhook empty + ALERT_EMAIL + SendGrid configured
    #     → send_alert dispatches via src.emails.send_email (webhook empty).
    alerts_mod._last_sent.clear()
    with mock.patch.object(metrics_mod.settings, "alert_webhook_url", ""), \
         mock.patch.object(metrics_mod.settings, "alert_email", "ops@stratoscan.com"), \
         mock.patch.object(metrics_mod.settings, "sendgrid_api_key", "SG.fake"), \
         mock.patch.object(metrics_mod.settings, "email_from_address", "noreply@stratoscan.com"), \
         mock.patch.object(
             alerts_mod.settings, "alert_webhook_min_interval_seconds", 600
         ), \
         mock.patch("src.emails.send_email", return_value=True) as m_send:
        sent = alerts_mod.send_alert(
            "Scheduler health alert", "stale jobs: [x]", level="error",
            throttle_key="scheduler-health")
        check("email channel dispatches True", sent is True)
        time.sleep(0.1)  # let the daemon thread deliver
        check("email sent via SendGrid client", m_send.called)
        if m_send.called:
            kwargs = m_send.call_args[0]
            check("email recipient = ALERT_EMAIL",
                  kwargs[0] == "ops@stratoscan.com", kwargs[0])
            check("email subject carries title+level",
                  "Scheduler health alert" in kwargs[1] and "ERROR" in kwargs[1],
                  kwargs[1])
            check("email body carries the detail", "stale jobs" in kwargs[2],
                  kwargs[2][:60])

    # 3f: email channel but SendGrid unconfigured → no-op (alerts_enabled False)
    alerts_mod._last_sent.clear()
    with mock.patch.object(metrics_mod.settings, "alert_webhook_url", ""), \
         mock.patch.object(metrics_mod.settings, "alert_email", "ops@stratoscan.com"), \
         mock.patch.object(metrics_mod.settings, "sendgrid_api_key", ""), \
         mock.patch.object(metrics_mod.settings, "email_from_address", ""), \
         mock.patch("src.emails.send_email") as m_send2:
        sent = alerts_mod.send_alert("x", "y")
        check("email w/o SendGrid → no-op False", sent is False)
        check("send_email never called", not m_send2.called)

    # 3g: email channel delivery failure degrades to a warning (never raises)
    alerts_mod._last_sent.clear()
    with mock.patch.object(metrics_mod.settings, "alert_webhook_url", ""), \
         mock.patch.object(metrics_mod.settings, "alert_email", "ops@stratoscan.com"), \
         mock.patch.object(metrics_mod.settings, "sendgrid_api_key", "SG.fake"), \
         mock.patch.object(metrics_mod.settings, "email_from_address", "noreply@stratoscan.com"), \
         mock.patch.object(
             alerts_mod.settings, "alert_webhook_min_interval_seconds", 0
         ), \
         mock.patch("src.emails.send_email", return_value=False) as m_send3:
        sent = alerts_mod.send_alert("email fail", "x", throttle_key="job:emailfail")
        check("email delivery failure doesn't raise", sent is True)
        time.sleep(0.1)
        check("failed email still attempted once", m_send3.call_count == 1)

    # ── 4. MetricsMiddleware route collapsing ──────────────────────────
    print("\n4. MetricsMiddleware")
    mw = MetricsMiddleware(app=None)
    # Pretend the router knows /stocks/{symbol}
    mw._templates = {"/stocks/{symbol}", "/health"}
    check("exact template matched", mw._route_label("/health") == "/health")
    check("concrete path → template", mw._route_label("/stocks/AAPL") == "/stocks/{symbol}")
    check("different id still collapses", mw._route_label("/stocks/MSFT") == "/stocks/{symbol}")
    check("unknown path → first segment", mw._route_label("/zzz/123") == "/zzz")

    # ── Results ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, cond, detail in results:
        icon = "✅" if cond else "❌"
        print(f"  {icon}  {name}: {detail}")
    print("\n" + "=" * 60)
    if ok:
        print("✅  ALL CHECKS PASSED")
    else:
        print("❌  SOME CHECKS FAILED")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
