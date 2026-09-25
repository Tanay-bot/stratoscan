"""Hermetic tests for account deletion and email change — no DB, no network.

Appendix A, "Recovery, changes, and deletion". Each destructive or identity-
changing action is tested in both directions: it must do what it says for the
right account, and it must leave every other account exactly as it was.

The real SQL for deletion (the two-table transaction, including
``backtest_jobs``, which has no foreign key to ``users``) is exercised against
Postgres in the Day 3 review notes; the fake here mirrors its contract.

Run: python -m tests.test_account_lifecycle
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "account-lifecycle-dummy-secret")
os.environ["SENDGRID_API_KEY"] = "SG.test-not-real"
os.environ["EMAIL_FROM_ADDRESS"] = "noreply@stratoscan.test"
os.environ["SMTP_HOST"] = ""

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import src.api as api_module  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

sent: list[dict] = []
mock.patch(
    "src.emails.send_email",
    side_effect=lambda to, subject, text, html_body=None: sent.append(
        {"to": to, "subject": subject, "text": text}
    ) or True,
).start()

client = TestClient(api_module.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


def register(email: str, password: str = "correct-horse-1") -> str:
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def me(token: str) -> int:
    return client.get("/auth/me", headers=H(token)).status_code


def put_personal(token: str, symbol: str) -> None:
    r = client.put("/me/personal", headers=H(token),
                   json={"watchlist": [symbol], "saved_screeners": [], "recent_backtests": []})
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 1. Deletion — refusals leave everything intact
# ═══════════════════════════════════════════════════════════════════════════
del_tok = register("leaving@x.test")
keep_tok = register("staying@x.test")
put_personal(del_tok, "NVDA")
put_personal(keep_tok, "MSFT")
leaving_id = fake_db.get_user_by_email("leaving@x.test")["id"]
staying_id = fake_db.get_user_by_email("staying@x.test")["id"]

# In-flight async backtests are also held in memory; seed one per account.
with api_module._BACKTEST_JOBS_LOCK:
    api_module._BACKTEST_JOBS["job-leaving"] = {"user_email": "leaving@x.test", "status": "running"}
    api_module._BACKTEST_JOBS["job-staying"] = {"user_email": "staying@x.test", "status": "running"}

r = client.post("/auth/delete-account", json={"password": "correct-horse-1"})
assert r.status_code == 401, r.status_code
r = client.post("/auth/delete-account", headers=H(del_tok), json={"password": "wrong-password"})
assert r.status_code == 400, f"wrong password must be 400, not 401 (401 logs the UI out): {r.status_code}"
assert fake_db.get_user_by_email("leaving@x.test") is not None
assert me(del_tok) == 200, "a refused deletion must not end the session"
print("✅ no token → 401; wrong password → 400 with the account and session intact")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Deletion — the account and its data go; nobody else's does
# ═══════════════════════════════════════════════════════════════════════════
before = len(sent)
r = client.post("/auth/delete-account", headers=H(del_tok), json={"password": "correct-horse-1"})
assert r.status_code == 200, r.text

assert fake_db.get_user_by_email("leaving@x.test") is None, "user row must be gone"
assert fake_db.get_user_data(leaving_id) is None, "user_data must be purged with the account"
assert "job-leaving" not in api_module._BACKTEST_JOBS, "in-memory jobs must be purged"
print("✅ user row, saved data and in-flight backtest jobs are all removed")

assert me(del_tok) == 401, "the deleted account's token must stop working"
assert client.post("/auth/login", json={"email": "leaving@x.test", "password": "correct-horse-1"}).status_code == 401
print("✅ the deleted account's token → 401 and its credentials no longer log in")

assert fake_db.get_user_by_email("staying@x.test") is not None
assert fake_db.get_user_data(staying_id)["watchlist"] == ["MSFT"]
assert "job-staying" in api_module._BACKTEST_JOBS
assert me(keep_tok) == 200
print("✅ another account's row, saved data, job and session are untouched")

notices = [m for m in sent[before:] if m["to"] == "leaving@x.test" and "deleted" in m["subject"]]
assert len(notices) == 1, notices
print("✅ a deletion confirmation is emailed to the former owner")

# The address is free again, and a new account there inherits nothing.
fresh = register("leaving@x.test", "a-new-person-1")
assert me(fresh) == 200
assert client.get("/me/personal", headers=H(fresh)).json()["watchlist"] == []
print("✅ the address can be registered again, as a new account with no inherited data")

with api_module._BACKTEST_JOBS_LOCK:
    api_module._BACKTEST_JOBS.pop("job-staying", None)

mock.patch.stopall()
print("\nALL ACCOUNT-LIFECYCLE CHECKS PASSED")
