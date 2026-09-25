"""Hermetic tests for password reset + change — no DB, no network, no mail.

Covers the Appendix A "Recovery" rows of the 15-day plan plus in-app password
change. The failure this suite exists to catch is a reset flow that *looks*
secure: a link that works twice, a stale link that still works, a reset that
leaves an attacker's session alive, or a response that tells a stranger
whether an address has an account.

Every rejection below is paired with an acceptance of the nearest valid case,
because a handler that returns 400 for everything passes every rejection test:

  - reused token rejected  ⟷  the same token worked once
  - old token rejected     ⟷  the newest token works
  - old session revoked    ⟷  the new password logs in, and a fresh token works
  - unknown address hidden ⟷  a known address really did get an email

Limit, stated rather than hidden: TestClient runs BackgroundTasks before it
returns, so the timing section proves the padded floor and the absence of a
DB-shaped difference, not that the email send is off the request path. The
latter is structural (``background_tasks.add_task``) and is checked against
the running stack in ``make verify``-style probes, not here.

Run: python -m tests.test_password_reset
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "password-reset-dummy-secret")
# A configured transport, so the reset path actually issues tokens. Nothing
# is sent: send_email is replaced below with a recorder.
os.environ["SENDGRID_API_KEY"] = "SG.test-not-real"
os.environ["EMAIL_FROM_ADDRESS"] = "noreply@stratoscan.test"
os.environ["SMTP_HOST"] = ""

from unittest import mock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import src.api as api_module  # noqa: E402
from src.auth import hash_reset_token  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

sent: list[dict] = []


def _record(to_email, subject, text_body, html_body=None):
    sent.append({"to": to_email, "subject": subject, "text": text_body})
    return True


mock.patch("src.emails.send_email", side_effect=_record).start()

# Most sections issue several resets for one account in quick succession;
# the per-account cooldown is tested on its own in section 9.
api_module.RESET_PER_ACCOUNT_COOLDOWN_SECONDS = 0
api_module.RESET_RESPONSE_FLOOR_SECONDS = 0.0

client = TestClient(api_module.app)
TOKEN_RE = re.compile(r"reset-password\?token=([A-Za-z0-9_\-]+)")


def register(email: str, password: str = "correct-horse-1") -> str:
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def login(email: str, password: str) -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def me(token: str) -> int:
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def request_reset(email: str):
    return client.post("/auth/password-reset/request", json={"email": email})


def reset_mails_to(email: str) -> list[dict]:
    return [m for m in sent if m["to"] == email and "Reset your" in m["subject"]]


def latest_token(email: str) -> str:
    mails = reset_mails_to(email)
    assert mails, f"no reset email reached {email}"
    match = TOKEN_RE.search(mails[-1]["text"])
    assert match, mails[-1]["text"]
    return match.group(1)


def confirm(token: str, new_password: str):
    return client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": new_password},
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. A reset is issued, emailed, and stored only as a hash
# ═══════════════════════════════════════════════════════════════════════════
register("alice@x.test")
r = request_reset("alice@x.test")
assert r.status_code == 200, r.text
token = latest_token("alice@x.test")
row = fake_db.get_user_by_email("alice@x.test")
assert row["reset_token_hash"] == hash_reset_token(token)
assert row["reset_token_hash"] != token, "the raw token must never be stored"
assert token not in str(row), "the raw token leaked into the stored row"
print("✅ reset email sent; only SHA-256(token) is stored, never the token")

expires = datetime.fromisoformat(row["reset_token_expires"])
ttl = expires - datetime.now(timezone.utc)
assert timedelta(minutes=29) < ttl <= timedelta(minutes=60), ttl
print(f"✅ link lifetime is inside the plan's 30–60 minute window ({int(ttl.total_seconds() // 60)} min)")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Unknown address: identical response, no email
# ═══════════════════════════════════════════════════════════════════════════
before = len(sent)
unknown = request_reset("nobody@x.test")
known = request_reset("alice@x.test")
assert unknown.status_code == known.status_code == 200
assert unknown.json() == known.json(), (unknown.json(), known.json())
assert not [m for m in sent[before:] if m["to"] == "nobody@x.test"]
assert reset_mails_to("alice@x.test")[-1] is sent[-1], "the known address really was emailed"
print("✅ unknown address → byte-identical body and status, and no email; known address emailed")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Success: password changes, old password dies, every session revoked
# ═══════════════════════════════════════════════════════════════════════════
dev1 = register("bob@x.test", "old-password-1")
dev2 = client.post(
    "/auth/login", json={"email": "bob@x.test", "password": "old-password-1"}
).json()["access_token"]
assert me(dev1) == 200 and me(dev2) == 200

request_reset("bob@x.test")
bob_token = latest_token("bob@x.test")
r = confirm(bob_token, "new-password-2")
assert r.status_code == 200, r.text

assert me(dev1) == 401 and me(dev2) == 401, "every pre-reset session must be revoked"
assert login("bob@x.test", "old-password-1") == 401, "old password must stop working"
assert login("bob@x.test", "new-password-2") == 200, "new password must work"
fresh = client.post(
    "/auth/login", json={"email": "bob@x.test", "password": "new-password-2"}
).json()["access_token"]
assert me(fresh) == 200, "a token minted after the reset must work"
print("✅ reset → both prior devices 401, old password dead, new password + fresh token work")

changed = [m for m in sent if m["to"] == "bob@x.test" and "was changed" in m["subject"]]
assert len(changed) == 1, changed
print("✅ owner is emailed that the password changed")

assert fake_db.get_user_by_email("bob@x.test")["email_verified"] is True
print("✅ completing a reset marks the email verified (the link proved control of it)")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Single use: the same token cannot be replayed
# ═══════════════════════════════════════════════════════════════════════════
r = confirm(bob_token, "attacker-password-3")
assert r.status_code == 400, r.text
assert login("bob@x.test", "new-password-2") == 200, "a replay must not change the password"
print("✅ reused token → 400, and the password the owner set is untouched")

# The race: two confirms reading the row before either writes. The guard is
# in the UPDATE's WHERE clause, so the second write matches nothing.
register("race@x.test")
request_reset("race@x.test")
race_hash = hash_reset_token(latest_token("race@x.test"))
race_id = fake_db.get_user_by_email("race@x.test")["id"]
first = fake_db.complete_password_reset(race_id, race_hash, "h1")
second = fake_db.complete_password_reset(race_id, race_hash, "h2")
assert first is not None and second is None, (first, second)
assert fake_db.get_user_by_email("race@x.test")["password_hash"] == "h1"
print("✅ concurrent confirms with one token → exactly one wins")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Only the most recent link works
# ═══════════════════════════════════════════════════════════════════════════
register("carol@x.test")
request_reset("carol@x.test")
older = latest_token("carol@x.test")
request_reset("carol@x.test")
newer = latest_token("carol@x.test")
assert older != newer
assert confirm(older, "carol-new-pass-1").status_code == 400, "a superseded link must die"
assert confirm(newer, "carol-new-pass-1").status_code == 200, "the newest link must work"
print("✅ two links requested → the older 400s, the newer succeeds")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Expiry, and tampering
# ═══════════════════════════════════════════════════════════════════════════
register("dave@x.test")
request_reset("dave@x.test")
dave_token = latest_token("dave@x.test")
dave = fake_db.get_user_by_email("dave@x.test")
fake_db._users["dave@x.test"]["reset_token_expires"] = (
    datetime.now(timezone.utc) - timedelta(minutes=1)
).isoformat()
r = confirm(dave_token, "dave-new-pass-1")
assert r.status_code == 400 and "expired" in r.json()["detail"].lower(), r.text
assert "request a new one" in r.json()["detail"].lower(), "expiry must point at a resend"
assert login("dave@x.test", "correct-horse-1") == 200, "an expired link must change nothing"
print("✅ expired link → 400 'expired… request a new one', password unchanged")

fake_db._users["dave@x.test"]["reset_token_expires"] = None
assert confirm(dave_token, "dave-new-pass-1").status_code == 400
print("✅ a token row with no expiry fails closed")

assert confirm("x" * 43, "whatever-pass-1").status_code == 400
tampered = newer[:-1] + ("A" if newer[-1] != "A" else "B")
assert confirm(tampered, "whatever-pass-1").status_code == 400
print("✅ forged and one-character-tampered tokens → 400")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Password policy applies to resets
# ═══════════════════════════════════════════════════════════════════════════
register("erin@x.test")
request_reset("erin@x.test")
r = confirm(latest_token("erin@x.test"), "short")
assert r.status_code == 422, r.text
assert login("erin@x.test", "correct-horse-1") == 200
assert confirm(latest_token("erin@x.test"), "long-enough-1").status_code == 200
print("✅ a too-short new password → 422 and the link survives to be used properly")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Timing: known and unknown addresses take the same time
# ═══════════════════════════════════════════════════════════════════════════
api_module.RESET_RESPONSE_FLOOR_SECONDS = 0.2
try:
    def timed(email: str) -> float:
        t0 = time.perf_counter()
        assert request_reset(email).status_code == 200
        return time.perf_counter() - t0

    known_t = min(timed("alice@x.test") for _ in range(3))
    unknown_t = min(timed("ghost@x.test") for _ in range(3))
finally:
    api_module.RESET_RESPONSE_FLOOR_SECONDS = 0.0
assert known_t >= 0.2 and unknown_t >= 0.2, (known_t, unknown_t)
assert abs(known_t - unknown_t) < 0.05, (known_t, unknown_t)
print(f"✅ both paths padded to the floor: known {known_t*1000:.0f}ms, unknown {unknown_t*1000:.0f}ms")


# ═══════════════════════════════════════════════════════════════════════════
# 9. Per-account cooldown: one victim cannot be mail-bombed
# ═══════════════════════════════════════════════════════════════════════════
api_module.RESET_PER_ACCOUNT_COOLDOWN_SECONDS = 60
try:
    register("frank@x.test")
    first = request_reset("frank@x.test")
    second = request_reset("frank@x.test")
    assert first.json() == second.json(), "a suppressed request must look identical"
    assert len(reset_mails_to("frank@x.test")) == 1, reset_mails_to("frank@x.test")
finally:
    api_module.RESET_PER_ACCOUNT_COOLDOWN_SECONDS = 0
assert request_reset("frank@x.test").status_code == 200
assert len(reset_mails_to("frank@x.test")) == 2, "the cooldown must lift"
print("✅ second request inside the cooldown → same response, no second email; lifts after")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Change password while signed in
# ═══════════════════════════════════════════════════════════════════════════
g1 = register("gina@x.test", "gina-pass-1")
g2 = client.post("/auth/login", json={"email": "gina@x.test", "password": "gina-pass-1"}).json()["access_token"]
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731

r = client.post("/auth/change-password", headers=H(g1),
                json={"current_password": "wrong-pass-9", "new_password": "gina-pass-2"})
assert r.status_code == 400, f"wrong current password must be 400, not 401 (401 logs the UI out): {r.status_code}"
assert me(g1) == 200, "a typo must not end the session"
print("✅ wrong current password → 400 and the session survives (a 401 would log the UI out)")

r = client.post("/auth/change-password", headers=H(g1),
                json={"current_password": "gina-pass-1", "new_password": "gina-pass-1"})
assert r.status_code == 400
print("✅ new password equal to the current one → 400")

request_reset("gina@x.test")
pending = latest_token("gina@x.test")
r = client.post("/auth/change-password", headers=H(g1),
                json={"current_password": "gina-pass-1", "new_password": "gina-pass-2"})
assert r.status_code == 200, r.text
new_token = r.json()["access_token"]
assert me(g2) == 401, "the other device must be signed out"
assert me(g1) == 401, "the token that made the change is also superseded"
assert me(new_token) == 200, "the returned token keeps this device signed in"
assert login("gina@x.test", "gina-pass-2") == 200 and login("gina@x.test", "gina-pass-1") == 401
print("✅ change → other device 401, returned token 200, new password works, old one doesn't")

assert confirm(pending, "attacker-undo-1").status_code == 400
assert login("gina@x.test", "gina-pass-2") == 200
print("✅ a reset link issued before a password change cannot undo it")

assert client.post("/auth/change-password",
                   json={"current_password": "a", "new_password": "bbbbbbbb"}).status_code == 401
print("✅ change-password without a token → 401")


# ═══════════════════════════════════════════════════════════════════════════
# 11. Routing: reset has its own rate-limit scope feeding the email budget
# ═══════════════════════════════════════════════════════════════════════════
assert api_module._scope_for_path("/auth/password-reset/request") == "auth_mail"
assert api_module._scope_for_path("/auth/password-reset/confirm") == "auth_mail"
assert api_module._scope_for_path("/auth/resend-verification") == "auth_mail"
assert api_module._scope_for_path("/auth/change-password") == "auth"
assert api_module.SCOPE_GLOBAL["auth_mail"] == "auth_register"
assert api_module.RATE_LIMITS["auth_mail"] == api_module.RATE_LIMITS["register"]
print("✅ reset + resend → 'auth_mail' scope, drawing on the planet-wide auth_register email budget")

mock.patch.stopall()
print("\nALL PASSWORD-RESET CHECKS PASSED")
