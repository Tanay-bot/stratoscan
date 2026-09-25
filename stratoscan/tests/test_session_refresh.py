"""Hermetic tests for sliding sessions (POST /auth/refresh) — no DB, no network.

Appendix A: "Token expires mid-session → refreshed silently on active use".
A refresh endpoint is easy to get dangerously wrong in two ways, and each has a
test here that a naive implementation fails:

  - a refresh that outlives a revocation (section 3): the stolen token keeps
    getting renewed after "log out everywhere";
  - a refresh with no ceiling (section 4): a stolen token, refreshed every few
    days, lives forever. ``auth_time`` is carried through refreshes, never
    reset, and a sign-in older than SESSION_MAX_AGE_DAYS cannot be extended.

Run: python -m tests.test_session_refresh
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "session-refresh-dummy-secret")
os.environ["SENDGRID_API_KEY"] = ""
os.environ["EMAIL_FROM_ADDRESS"] = ""

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import src.api as api_module  # noqa: E402
from src.auth import create_access_token, verify_access_token  # noqa: E402
from src.config import settings  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

client = TestClient(api_module.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
DAY = 86400


def refresh(token: str):
    return client.post("/auth/refresh", headers=H(token))


def me(token: str) -> int:
    return client.get("/auth/me", headers=H(token)).status_code


r = client.post("/auth/register", json={"email": "slide@x.test", "password": "correct-horse-1"})
assert r.status_code == 200, r.text
tok = r.json()["access_token"]
uid = fake_db.get_user_by_email("slide@x.test")["id"]


# ═══════════════════════════════════════════════════════════════════════════
# 1. A valid token is re-issued; auth_time is carried, not reset
# ═══════════════════════════════════════════════════════════════════════════
old = verify_access_token(tok)
time.sleep(1.1)  # so the new token's iat/exp are observably later
r = refresh(tok)
assert r.status_code == 200, r.text
new_tok = r.json()["access_token"]
new = verify_access_token(new_tok)
assert me(new_tok) == 200
assert new["exp"] > old["exp"], "refresh must push the expiry out"
assert new["auth_time"] == old["auth_time"], "refresh must NOT reset auth_time"
assert new["ver"] == old["ver"], "refresh must keep the token_version"
print("✅ refresh → later expiry, same auth_time, same ver; the new token works")

assert client.post("/auth/refresh").status_code == 401
assert refresh("not.a.token").status_code == 401
print("✅ no token / garbage token → 401")


# ═══════════════════════════════════════════════════════════════════════════
# 2. A refreshed token is still revocable
# ═══════════════════════════════════════════════════════════════════════════
fake_db.bump_token_version(uid)
assert me(new_tok) == 401, "a refreshed token must die with a revocation like any other"
print("✅ logout-all revokes refreshed tokens too")


# ═══════════════════════════════════════════════════════════════════════════
# 3. A refresh cannot outlive a revocation
# ═══════════════════════════════════════════════════════════════════════════
assert refresh(tok).status_code == 401, "a revoked token must not be refreshable"
assert refresh(new_tok).status_code == 401
print("✅ a revoked token cannot be refreshed back to life")

live = client.post("/auth/login", json={"email": "slide@x.test", "password": "correct-horse-1"}).json()["access_token"]
assert refresh(live).status_code == 200, "a token minted after the revocation refreshes normally"
print("✅ ...while a token minted after it refreshes normally")


# ═══════════════════════════════════════════════════════════════════════════
# 4. The ceiling: a sign-in cannot be extended past SESSION_MAX_AGE_DAYS
# ═══════════════════════════════════════════════════════════════════════════
ver = fake_db.get_user_by_email("slide@x.test")["token_version"]
cap = settings.session_max_age_days
now = int(time.time())

just_inside = create_access_token(uid, "slide@x.test", ver, auth_time=now - (cap * DAY - 3600))
r = refresh(just_inside)
assert r.status_code == 200, r.text
carried = verify_access_token(r.json()["access_token"])["auth_time"]
assert carried == now - (cap * DAY - 3600), "the old auth_time must ride along, not reset"
print(f"✅ a sign-in one hour short of {cap} days refreshes, and keeps its original auth_time")

too_old = create_access_token(uid, "slide@x.test", ver, auth_time=now - (cap * DAY + 60))
assert me(too_old) == 200, "the old token itself is still valid until it expires"
r = refresh(too_old)
assert r.status_code == 401 and "log in again" in r.json()["detail"].lower(), r.text
print(f"✅ a sign-in older than {cap} days cannot be extended (the token still works until exp)")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Tokens minted before auth_time existed fall back to iat
# ═══════════════════════════════════════════════════════════════════════════
def legacy_token(iat: int) -> str:
    import base64, hashlib, hmac, json  # noqa: E401
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    head = enc(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = enc(json.dumps({"sub": str(uid), "email": "slide@x.test", "ver": ver,
                           "iat": iat, "exp": int(time.time()) + DAY}).encode())
    sig = hmac.new(settings.jwt_secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{enc(sig)}"


assert "auth_time" not in verify_access_token(legacy_token(now))
assert refresh(legacy_token(now - DAY)).status_code == 200
assert refresh(legacy_token(now - (cap + 1) * DAY)).status_code == 401
print("✅ a pre-auth_time token uses iat for the ceiling (recent → 200, too old → 401)")

mock.patch.stopall()
print("\nALL SESSION-REFRESH CHECKS PASSED")
