"""Day 13 — failure drills 5, 6 and 7 (hermetic). Each must DEGRADE, never 500.

Drills 1–4 (Finnhub keys dead, every LLM provider down, yfinance timeout,
Upstash down) live in ``verify_failure_drills.py`` (also hermetic — failures
injected at the real boundaries; run by the release gate). These three
are new and need no network:

  5. Mail transport down — SendGrid unreachable (and SMTP refusing).
     Register still succeeds and says the email was not sent; a password
     reset answers exactly as it always does (no account-existence leak);
     resend-verification says so plainly; the digest stops (its own drill
     lives in test_weekly_digest).
  6. Error capture failing — the error_log write and analytics both raise.
     Normal requests are unaffected; a request that does fail still gets
     the same generic body it always got, with no extra detail.
  7. Database unreachable mid-request — the account loads, then the next
     query fails (Neon waking, network, pool exhausted). 503 + Retry-After,
     never 500, never a 401 that would log a signed-in person out.

Run: python -m tests.test_failure_drills
"""

from __future__ import annotations

import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "drills-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "true"
os.environ["SENDGRID_API_KEY"] = "SG.drill-not-a-real-key"
os.environ["EMAIL_FROM_ADDRESS"] = "noreply@x.test"
os.environ["SMTP_HOST"] = ""

from unittest import mock  # noqa: E402

import psycopg2  # noqa: E402
import psycopg2.pool  # noqa: E402

from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from fastapi.testclient import TestClient  # noqa: E402

import src.api as api  # noqa: E402

client = TestClient(api.app, raise_server_exceptions=False)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731

# ═══════════════════════════════════════════════════════════════════════════
# Drill 5 — mail transport down
# ═══════════════════════════════════════════════════════════════════════════
calls = []


def mail_down(*a, **k):
    calls.append(1)
    raise urllib.error.URLError("connection refused (drill)")


with mock.patch("urllib.request.urlopen", side_effect=mail_down):
    r = client.post("/auth/register", json={"email": "m@x.test", "password": "correct-horse-1"})
    assert r.status_code == 200, r.text
    assert r.json()["email_sent"] is False, "register says the email was NOT sent"
    tok = r.json()["access_token"]
    assert calls, "the transport really was attempted (and failed)"
    known = client.post("/auth/password-reset/request", json={"email": "m@x.test"})
    unknown = client.post("/auth/password-reset/request", json={"email": "nobody@x.test"})
    assert known.status_code == unknown.status_code == 200, (known.text, unknown.text)
    assert known.json() == unknown.json(), "mail down must not reveal which addresses exist"
    fake_db._user(fake_db.get_user_by_email("m@x.test")["id"])["email_verified"] = False
    resend = client.post("/auth/resend-verification", headers=H(tok))
    assert resend.status_code in (200, 502, 503), resend.status_code
    assert resend.status_code != 500 and "connection refused" not in resend.text
print("✅ drill 5 — mail down: signup succeeds and says no email went out; password reset answers "
      "identically for known and unknown addresses; resend fails cleanly")

# SMTP refusing instead of SendGrid: same outcome.
with mock.patch.object(api.settings, "smtp_host", "127.0.0.1"), \
        mock.patch("smtplib.SMTP", side_effect=ConnectionRefusedError("drill")):
    r = client.post("/auth/register", json={"email": "s@x.test", "password": "correct-horse-1"})
    assert r.status_code == 200 and r.json()["email_sent"] is False, r.text
print("✅ drill 5 — SMTP refusing: same clean degradation")

# ═══════════════════════════════════════════════════════════════════════════
# Drill 6 — error capture failing
# ═══════════════════════════════════════════════════════════════════════════


def capture_down(*a, **k):
    raise psycopg2.OperationalError("error_log unreachable (drill)")


with mock.patch.object(fake_db, "insert_error", side_effect=capture_down, create=True), \
        mock.patch.object(fake_db, "insert_event", side_effect=RuntimeError("analytics down"), create=True):
    ok = client.post("/auth/register", json={"email": "c@x.test", "password": "correct-horse-1"})
    assert ok.status_code == 200, f"a failing event write broke signup: {ok.status_code}"
    assert client.get("/meta").status_code == 200
    # A real bug while capture is down: the same generic 500 as ever — the
    # failed capture adds nothing and never turns into a different error.

    @api.app.get("/__drill_boom")
    async def _boom():
        raise RuntimeError("secret internal detail")

    boom = client.get("/__drill_boom")
    assert boom.status_code == 500, boom.status_code
    assert boom.json() == {"error": True, "detail": "Internal server error. Please try again later.",
                           "status_code": 500}, boom.json()
    assert "secret internal detail" not in boom.text
print("✅ drill 6 — error capture failing: normal requests unaffected; a genuine bug still gets the same "
      "generic body; the capture failure itself never surfaces")

# ═══════════════════════════════════════════════════════════════════════════
# Drill 7 — the database goes away mid-request
# ═══════════════════════════════════════════════════════════════════════════
tok = client.post("/auth/register", json={"email": "d@x.test", "password": "correct-horse-1"}).json()["access_token"]
OUTAGES = [
    psycopg2.OperationalError("server closed the connection unexpectedly"),
    psycopg2.InterfaceError("connection already closed"),
    psycopg2.pool.PoolError("connection pool exhausted"),
]
MID_REQUEST = [  # (method, path, body, the fake-DB method the handler reaches AFTER auth)
    ("GET", "/me/strategies", None, "list_strategies"),
    ("GET", "/me/email-preferences", None, "get_weekly_digest"),
    ("GET", "/me/entitlements", None, "get_usage"),
    ("GET", "/me/personal", None, "get_user_data"),
    ("POST", "/backtest/async", {"query": "oversold large cap stocks"}, "consume_usage"),
]
for exc in OUTAGES:
    for method, path, body, method_name in MID_REQUEST:
        with mock.patch.object(fake_db, method_name, side_effect=exc, create=True):
            r = client.request(method, path, json=body, headers=H(tok))
        assert r.status_code == 503, f"{type(exc).__name__} in {path}: {r.status_code} {r.text[:120]}"
        assert r.headers.get("retry-after"), f"{path}: a 503 tells the client when to retry"
        assert "closed" not in r.text and "exhausted" not in r.text, "no raw driver text"
    # The account lookup itself failing is an outage too — NOT "account gone".
    with mock.patch.object(fake_db, "get_user_by_id", side_effect=exc):
        r = client.get("/auth/me", headers=H(tok))
    assert r.status_code == 503, f"{type(exc).__name__} loading the user -> {r.status_code}"
print(f"✅ drill 7 — database unreachable mid-request: {len(MID_REQUEST)} routes × {len(OUTAGES)} kinds of "
      "outage → 503 + Retry-After, never 500; a failed account lookup is 503, never a 401 that logs people out")

# And the session survives: once the database is back, the same token works.
assert client.get("/auth/me", headers=H(tok)).status_code == 200
assert client.get("/health").status_code == 200
print("✅ after the outage the same session works — nobody was logged out")

mock.patch.stopall()
print("\nALL FAILURE DRILLS (5–7) PASSED")
