"""Hermetic tests for the Stratoscan admin dashboard (role-gated /admin API).

Run: python -m tests.test_admin

Fully hermetic — zero network, zero Neon, zero real LLM:

1. require_admin dependency semantics: 401 without a token, 401 with a
   forged/expired token, 403 for a NON-admin user, 200 for an admin.
2. ADMIN_EMAILS bootstrap: logging in with an email listed in ADMIN_EMAILS
   grants the admin role (idempotent) and the /admin endpoints then work.
3. GET /admin/overview: aggregates DB health, stock counts, user stats,
   job monitor summary and config warnings — with a fake PostgresDB so no
   socket is opened; degrades gracefully (alive=False, error surfaced).
4. GET /admin/users: paginated + searchable, newest first, never leaks
   password hashes.
5. PATCH /admin/users/{id}: set plan, grant/revoke admin, mark verified;
   self-demotion guard (an admin can't revoke their own role); unknown
   user → 404; empty body → 422; non-admin caller → 403.
6. GET /admin/jobs: job-monitor summary via the real app (in-memory store).
7. GET /admin (HTML): serves the dashboard shell.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so this
# suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

# Hermetic: /admin/overview gates its DB gather behind
# settings.has_database_url BEFORE touching PostgresDB (patched below with
# FakeAdminDB). On a clean CI runner with no .env that guard 503s first —
# point database_url at a dummy so the guard passes and the fake serves
# health/stocks/users (mirrors test_api_dirty_data's settings redirection).
from src.config import settings  # noqa: E402
settings.database_url = "postgresql://ci-dummy-stratoscan"

from unittest import mock  # noqa: E402

from src.auth import hash_password  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402


class FakeAdminDB(FakeUserDB):
    """FakeUserDB + the stock-overview methods /admin/overview calls."""

    def __init__(self, stock_count: int = 3, stale_count: int = 1) -> None:
        super().__init__()
        self._stock_count = stock_count
        self._stale_count = stale_count

    def health_check(self) -> dict:
        return {"alive": True, "latency_ms": 12.5, "error": None}

    def stock_count(self) -> int:
        return self._stock_count

    def data_freshness(self, max_age_minutes: int = 15) -> dict:
        return {
            "oldest_age_minutes": 4.0,
            "max_updated_at": "2026-08-01T12:00:00Z",
            "stale_count": self._stale_count,
            "total_rows": self._stock_count,
        }


fake_db = FakeAdminDB()
# Seed: one admin (the acting user) + two regular users.
admin_id = fake_db.create_user(
    "admin@stratoscan.app", hash_password("adminpass123"),
    plan="free", email_verified=True, is_admin=True,
)
fake_db.create_user(
    "bob@stratoscan.app", hash_password("bobpass1234"),
    plan="free", email_verified=False,
)
fake_db.create_user(
    "carol@stratoscan.app", hash_password("carolpass123"),
    plan="premium", email_verified=True,
)

mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from src.api import app  # noqa: E402

client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)


def _login(email: str, password: str) -> str:
    """Log in and return the JWT."""
    r = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 1. require_admin: auth semantics ─────────────────────────────────────────
print("1. require_admin auth semantics...")
r = client.get("/admin/overview")
assert r.status_code == 401, f"no token must 401: {r.status_code}"
r = client.get("/admin/overview", headers=_auth_headers("forged.token.here"))
assert r.status_code == 401, f"forged token must 401: {r.status_code}"

bob_tok = _login("bob@stratoscan.app", "bobpass1234")
r = client.get("/admin/overview", headers=_auth_headers(bob_tok))
assert r.status_code == 403, f"non-admin must 403: {r.status_code} {r.text[:200]}"

admin_tok = _login("admin@stratoscan.app", "adminpass123")
r = client.get("/admin/overview", headers=_auth_headers(admin_tok))
assert r.status_code == 200, f"admin must 200: {r.status_code} {r.text[:200]}"
print("✅ 401 no token / 401 forged / 403 non-admin / 200 admin")


# ── 2. ADMIN_EMAILS bootstrap at login — only for a VERIFIED address ─────────
print("2. ADMIN_EMAILS bootstrap...")


def _login_as_listed(email: str, password: str, *, gate: bool = False):
    with mock.patch("src.api.settings") as ms:
        ms.jwt_secret = os.environ.get("JWT_SECRET", "test-secret")
        ms.jwt_expire_hours = 24 * 7
        ms.admin_email_list = [email]
        ms.email_verification_required = gate
        return client.post("/auth/login", json={"email": email, "password": password})


# bob is seeded email_verified=False. Listed but unverified → NOT promoted.
# (Before Day 3 this granted admin: an address is not an identity until its
# inbox has been proved, and ADMIN_EMAILS names an address.)
r = _login_as_listed("bob@stratoscan.app", "bobpass1234")
assert r.status_code == 200, r.text
assert r.json()["user"]["is_admin"] is False, "unverified listed address must NOT be promoted"
assert fake_db.get_user_by_email("bob@stratoscan.app")["is_admin"] is False
print("✅ listed but unverified → login succeeds, admin NOT granted")

# ...and the same login after verification → promoted. The pair is what shows
# the check is on verification, not a blanket refusal.
fake_db.mark_email_verified(fake_db.get_user_by_email("bob@stratoscan.app")["id"])
r = _login_as_listed("bob@stratoscan.app", "bobpass1234")
assert r.status_code == 200, r.text
bob_row = fake_db.get_user_by_email("bob@stratoscan.app")
assert bob_row["is_admin"] is True, "a verified ADMIN_EMAILS login must grant admin"
r = client.get("/admin/overview", headers=_auth_headers(bob_tok))
assert r.status_code == 200, f"newly-promoted bob must 200: {r.status_code}"
r = _login_as_listed("bob@stratoscan.app", "bobpass1234")  # idempotent
assert r.status_code == 200, r.text
print("✅ once verified → bootstrap grants admin at login + idempotent")

# ── 2b. ADMIN_EMAILS bootstrap at REGISTER ───────────────────────────────────
print("2b. ADMIN_EMAILS bootstrap at register...")


def _register_as_listed(email: str, *, mail_configured: bool):
    with mock.patch("src.api.settings") as ms:
        ms.jwt_secret = os.environ.get("JWT_SECRET", "test-secret")
        ms.jwt_expire_hours = 24 * 7
        ms.admin_email_list = [email]
        ms.email_verification_required = False
        # No transport → accounts start verified (there is no link to click),
        # so the operator's local bootstrap stays one step, as before.
        ms.has_email_verification = mail_configured
        ms.email_verification_token_ttl_hours = 24
        with mock.patch("src.emails.send_verification_email", return_value=True):
            return client.post("/auth/register", json={"email": email, "password": "listedpass123"})


r = _register_as_listed("dana@stratoscan.app", mail_configured=False)
assert r.status_code == 200, r.text
assert r.json()["user"]["is_admin"] is True, "no mail transport → verified at birth → admin"
assert fake_db.get_user_by_email("dana@stratoscan.app")["is_admin"] is True
print("✅ no mail transport: register grants admin in the same response (dev bootstrap unchanged)")

# THE ESCALATION this closes. With mail configured, a stranger registers the
# operator's address before the operator does. They cannot read that inbox,
# so they must get an ordinary, unverified, non-admin account.
r = _register_as_listed("founder@stratoscan.app", mail_configured=True)
assert r.status_code == 200, r.text
body = r.json()
assert body["user"]["email_verified"] is False
assert body["user"]["is_admin"] is False, "an unverified registrant of a listed address must NOT be admin"
assert fake_db.get_user_by_email("founder@stratoscan.app")["is_admin"] is False
r = client.get("/admin/users", headers=_auth_headers(body["access_token"]))
assert r.status_code == 403, f"squatter must not reach the admin API: {r.status_code}"
print("✅ squatting a listed address with mail on → no admin, /admin/users 403")

# No exemption from the verification gate for listed addresses any more: it
# existed only because the grant happened at login; it now let a squatter in.
eve_id = fake_db.create_user(
    "eve@stratoscan.app", hash_password("evepass1234"), plan="free", email_verified=False,
)
r = _login_as_listed("eve@stratoscan.app", "evepass1234", gate=True)
assert r.status_code == 403, f"listed + unverified + gate ON must be 403: {r.status_code} {r.text[:200]}"
assert fake_db.get_user_by_email("eve@stratoscan.app")["is_admin"] is False
fake_db.mark_email_verified(eve_id)
r = _login_as_listed("eve@stratoscan.app", "evepass1234", gate=True)
assert r.status_code == 200 and r.json()["user"]["is_admin"] is True, r.text
print("✅ gate ON: listed + unverified → 403; the same account once verified → 200 + admin")

# Later sections pin a fixture of exactly four users (admin, bob, carol, dana);
# remove the two accounts this section created to probe the escalation.
for _probe in ("founder@stratoscan.app", "eve@stratoscan.app"):
    fake_db.delete_user(fake_db.get_user_by_email(_probe)["id"], _probe)


# ── 3. Overview aggregation ─────────────────────────────────────────────────
print("3. Admin overview aggregation...")
r = client.get("/admin/overview", headers=_auth_headers(admin_tok))
assert r.status_code == 200, r.text
o = r.json()
assert o["status"] == "ok"
assert o["database"]["configured"] is True and o["database"]["alive"] is True
assert o["database"]["latency_ms"] == 12.5
assert o["stocks"]["count"] == 3
assert o["stocks"]["freshness"]["stale_count"] == 1
# Users must match the DB's own aggregation EXACTLY — self-consistent, so
# the assertion can never drift when earlier sections promote more admins.
assert o["users"] == fake_db.user_stats(), (o["users"], fake_db.user_stats())
assert "jobs" in o and "scheduler" in o["jobs"]
assert isinstance(o["warnings"], list)
assert o["config"]["database"] is True
assert "admin_emails" in o["config"]
print("✅ overview aggregates db/stocks/users/jobs/config")

# Isolated aggregation: a pristine DB with exactly one admin must report
# exactly one admin (proves the endpoint reads the DB, not shared state).
pristine = FakeAdminDB(stock_count=5, stale_count=2)
pristine.create_user(
    "admin@stratoscan.app", hash_password("adminpass123"),
    plan="free", email_verified=False, is_admin=True,
)
pristine.create_user(
    "zoe@stratoscan.app", hash_password("zoepass1234"),
    plan="free", email_verified=False,
)
with mock.patch("src.database.PostgresDB", return_value=pristine):
    r = client.post(
        "/auth/login",
        json={"email": "admin@stratoscan.app", "password": "adminpass123"},
    )
    assert r.status_code == 200, r.text
    pristine_tok = r.json()["access_token"]
    r = client.get("/admin/overview", headers=_auth_headers(pristine_tok))
assert r.status_code == 200, r.text
assert r.json()["users"] == {
    "total": 2, "admins": 1, "verified": 0, "plans": {"free": 2},
}, r.json()["users"]
assert r.json()["stocks"]["count"] == 5
assert r.json()["stocks"]["freshness"]["stale_count"] == 2
print("✅ isolated overview on a pristine DB reports exactly 1 admin")

# Overview degrades gracefully when the DB is down. The token must be
# minted against `broken` INSIDE the patch block — an admin_tok minted
# against the shared fake_db would 401 because _fetch_user(user_id)
# queries whatever PostgresDB() currently returns (the empty `broken`).
broken = FakeAdminDB()
broken.create_user(
    "admin@stratoscan.app", hash_password("adminpass123"),
    plan="free", email_verified=False, is_admin=True,
)
broken.health_check = lambda: {"alive": False, "latency_ms": None, "error": "db down"}
broken.stock_count = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
with mock.patch("src.database.PostgresDB", return_value=broken):
    r = client.post(
        "/auth/login",
        json={"email": "admin@stratoscan.app", "password": "adminpass123"},
    )
    assert r.status_code == 200, r.text
    broken_tok = r.json()["access_token"]
    r = client.get("/admin/overview", headers=_auth_headers(broken_tok))
assert r.status_code == 200, r.text
assert r.json()["database"]["alive"] is False
assert "boom" in r.json()["database"]["error"]
print("✅ overview degrades gracefully on DB failure")


# ── 4. User list (pagination + search, no password leaks) ───────────────────
print("4. Admin user list...")
r = client.get("/admin/users", headers=_auth_headers(admin_tok))
assert r.status_code == 200, r.text
body = r.json()
# dana (registered in section 2b) is now the newest user.
assert body["total"] == 4
assert len(body["users"]) == 4
emails = [u["email"] for u in body["users"]]
assert emails == [
    "dana@stratoscan.app",
    "carol@stratoscan.app",
    "bob@stratoscan.app",
    "admin@stratoscan.app",
], emails
# Never leak password hashes or tokens.
for u in body["users"]:
    assert "password_hash" not in u and "verification_token" not in u, u

r = client.get("/admin/users?search=bob", headers=_auth_headers(admin_tok))
body = r.json()
assert body["total"] == 1 and body["users"][0]["email"] == "bob@stratoscan.app", body

r = client.get("/admin/users?limit=1&offset=2", headers=_auth_headers(admin_tok))
body = r.json()
assert body["total"] == 4 and len(body["users"]) == 1
# Newest-first: dana(4), carol(3), bob(2), admin(1) → offset 2 = bob.
assert body["users"][0]["email"] == "bob@stratoscan.app", body
print("✅ list: newest-first, searchable, paginated, hash-free")


# ── 5. PATCH user management ────────────────────────────────────────────────
print("5. Admin PATCH user management...")
bob_id = fake_db.get_user_by_email("bob@stratoscan.app")["id"]
carol_id = fake_db.get_user_by_email("carol@stratoscan.app")["id"]

# Set plan.
r = client.patch(
    f"/admin/users/{bob_id}", json={"plan": "premium"},
    headers=_auth_headers(admin_tok),
)
assert r.status_code == 200 and r.json()["plan"] == "premium", r.text
assert fake_db.get_user_by_id(bob_id)["plan"] == "premium"

# Grant admin (bob already admin from bootstrap; use carol).
r = client.patch(
    f"/admin/users/{carol_id}", json={"is_admin": True},
    headers=_auth_headers(admin_tok),
)
assert r.status_code == 200 and r.json()["is_admin"] is True, r.text

# Revoke admin.
r = client.patch(
    f"/admin/users/{carol_id}", json={"is_admin": False},
    headers=_auth_headers(admin_tok),
)
assert r.status_code == 200 and r.json()["is_admin"] is False, r.text

# Mark verified.
r = client.patch(
    f"/admin/users/{bob_id}", json={"email_verified": True},
    headers=_auth_headers(admin_tok),
)
assert r.status_code == 200 and r.json()["email_verified"] is True, r.text

# Self-demotion guard: the admin cannot revoke their own role.
r = client.patch(
    f"/admin/users/{admin_id}", json={"is_admin": False},
    headers=_auth_headers(admin_tok),
)
assert r.status_code == 400, f"self-demotion must 400: {r.status_code}"
assert "own admin" in r.json()["detail"].lower(), r.text

# Unknown user → 404.
r = client.patch(
    "/admin/users/999999", json={"plan": "free"},
    headers=_auth_headers(admin_tok),
)
assert r.status_code == 404, f"unknown user must 404: {r.status_code}"

# Empty body → 422.
r = client.patch(
    f"/admin/users/{carol_id}", json={}, headers=_auth_headers(admin_tok),
)
assert r.status_code == 422, f"empty patch must 422: {r.status_code}"

# Non-admin → 403 (carol was demoted back).
carol_tok = _login("carol@stratoscan.app", "carolpass123")
r = client.patch(
    f"/admin/users/{bob_id}", json={"plan": "free"},
    headers=_auth_headers(carol_tok),
)
assert r.status_code == 403, f"non-admin patch must 403: {r.status_code}"
print("✅ patch: plan/admin/verify + self-demotion guard + 404/422/403")


# ── 6. Jobs panel ───────────────────────────────────────────────────────────
print("6. Admin jobs panel...")
r = client.get("/admin/jobs", headers=_auth_headers(admin_tok))
assert r.status_code == 200, r.text
j = r.json()
assert "jobs" in j and "scheduler" in j
assert any(job["job_name"] == "price_refresh" for job in j["jobs"])
print("✅ /admin/jobs returns the job-monitor summary")


# ── 7. Dashboard HTML ───────────────────────────────────────────────────────
print("7. Dashboard HTML...")
r = client.get("/admin")
assert r.status_code == 200, r.text
html = r.text
assert "Stratoscan" in html and "<html" in html.lower()
assert "login" in html.lower() and "admin/overview" in html
print("✅ /admin serves the self-contained dashboard shell")


mock.patch.stopall()
print()
print("🎯 All admin-dashboard tests pass!")
