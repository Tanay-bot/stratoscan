"""Hermetic behavioral test of the Alembic migration chain.

No Postgres exists on this machine and we must never touch the production
Neon DB, so this drives the REAL migration surface via Alembic's offline
SQL generation (``--sql``), which emits the exact SQL that would execute
against a database — exercising env.py, revision ordering, and every
migration's actual SQL — without connecting.

Scenarios verified:
  1. FRESH DB:  ``alembic upgrade head --sql`` — the full chain a brand-new
     database runs. Must NOT create stripe_customer_id anywhere, MUST drop
     it IF EXISTS (a mid-chain migration's cleanup), and MUST add the
     email-verification columns (the head migration).
  2. ALREADY-MIGRATED dev DB: ``alembic upgrade d5e6f7a8b9c0:e7f8a9b0c1d2
     --sql`` — a DB that applied the OLD stripe-era head (column exists)
     upgrading past it runs ONLY the drop. Must emit exactly one DROP.
  3. REVERSIBILITY: ``alembic downgrade a2b4c6d8e0f2:base --sql`` — the
     full downgrade from the current head to base must succeed (the
     stripe-era migration re-adds its column, the email-verification
     migration drops its columns — the documented reversibility contract).

Then a LIVE HTTP probe (TestClient, real FastAPI app) confirms the auth
surface still works end to end: register -> /auth/me -> /backtest auth
gate (401 without token).

Fully hermetic: offline mode never connects, and the subprocess env is
seeded with a dummy DATABASE_URL so the real one in .env is never used.

Run: python -m tests.test_migration_behavior
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so the
# live-HTTP section never touches the real Upstash Redis shared buckets.
# Without this, /backtest in section 4 can 429 (daily bucket exhausted by
# other suites sharing the testclient IP) instead of hitting the auth gate.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
# Disable email verification so the live register in section 4 never
# attempts a real SendGrid call (send_email catches failures, but a
# hermetic suite must not open a network socket at all).
os.environ["SENDGRID_API_KEY"] = ""
os.environ["EMAIL_FROM_ADDRESS"] = ""

_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
_DUMMY_URL = "postgresql://verify:verify@127.0.0.1:5432/never_used"
checks = []


def check(name: str, cond: bool, detail: str = ""):
    checks.append((name, bool(cond)))
    print(f"{'✅' if cond else '❌'} {name}{' — ' + detail if detail else ''}")
    if not cond:
        raise AssertionError(f"FAILED: {name}")


def alembic_sql(args: list[str]) -> str:
    """Run alembic offline SQL generation; returns the emitted SQL."""
    env = dict(os.environ)
    env["DATABASE_URL"] = _DUMMY_URL  # offline mode only configures the dialect
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],  # same interpreter as the test process
        capture_output=True,
        text=True,
        cwd=_PROJECT_ROOT,  # alembic.ini lives at the project root, not in tests/
        env=env,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed:\n{proc.stderr[-1500:]}")
    return proc.stdout


# ═══════════════════════════════════════════════════════════════════════════
# 1. FRESH DB — full chain to head
# ═══════════════════════════════════════════════════════════════════════════
print("── 1. Fresh DB: full `alembic upgrade head` SQL ──")
fresh = alembic_sql(["upgrade", "head"])
check("fresh-chain SQL generated", len(fresh) > 100)

# The dead column must never be created on a fresh DB: no CREATE TABLE or
# ADD COLUMN statement may reference stripe_customer_id.
adds = re.findall(r"(?i)(?:create\s+table|add\s+column)[^;]*stripe_customer_id[^;]*;", fresh)
check("no CREATE TABLE / ADD COLUMN touches stripe_customer_id", not adds, f"{adds[:2]}")
drops = re.findall(r"(?i)drop\s+column\s+if\s+exists\s+stripe_customer_id", fresh)
check("head migration DROPs stripe_customer_id IF EXISTS", len(drops) == 1, f"{len(drops)} drops")

# users table must be created without the column (spot-check the CREATE).
# Case-insensitive: alembic emits ``CREATE TABLE IF NOT EXISTS users (``.
users_create = re.search(r"(?is)create\s+table\s+if\s+not\s+exists\s+users.*?;", fresh)
if users_create:
    check("users CREATE TABLE omits stripe_customer_id",
          "stripe_customer_id" not in users_create.group(0).lower())
else:
    check("users CREATE TABLE found", False, "no CREATE TABLE users in fresh chain")

# All revisions must appear in order (chain exercised end-to-end).
for rev in ("b3e7ca01c230", "a1c2d3e4f506", "c9e1f2a3b4c5", "d5e6f7a8b9c0", "e7f8a9b0c1d2", "f1a2b3c4d5e6", "f7a8b9c0d1e2", "a2b4c6d8e0f2", "b3c5d7e9f1a3", "b4c5d6e7f8a9"):
    check(f"revision {rev} executed in fresh chain", rev in fresh)

# The job-monitoring migration must create its tables on a fresh DB.
job_create = re.search(r"(?is)create\s+table\s+if\s+not\s+exists\s+job_runs.*?;", fresh)
check("job_runs table created in fresh chain", bool(job_create))
check("scheduler_heartbeat table created in fresh chain",
      bool(re.search(r"(?is)create\s+table\s+if\s+not\s+exists\s+scheduler_heartbeat", fresh)))

# The head of the chain is now the user-data migration (alembic offline
# mode emits a "Running upgrade <from> -> <to>" header per step).
check("head revision is b4c5d6e7f8a9",
      bool(re.search(r"Running upgrade b3c5d7e9f1a3\s+->\s+b4c5d6e7f8a9", fresh)))
# The head migration creates the per-user saved-state table (frontend sync).
user_data_create = re.search(r"(?is)create\s+table\s+if\s+not\s+exists\s+user_data.*?;", fresh)
check("user_data table created in fresh chain", bool(user_data_create))
# The email-verification migration ADDs the email_verified column (signup flow).
email_adds = re.findall(r"(?i)add\s+column\s+if\s+not\s+exists\s+email_verified", fresh)
check("email-verification migration adds email_verified",
      len(email_adds) == 1, f"{len(email_adds)} adds")
# The head migration ADDs the admin-role column (dashboard gate).
admin_adds = re.findall(r"(?i)add\s+column\s+if\s+not\s+exists\s+is_admin", fresh)
check("admin-role migration adds is_admin", len(admin_adds) == 1, f"{len(admin_adds)} adds")

# ═══════════════════════════════════════════════════════════════════════════
# 2. ALREADY-MIGRATED dev DB (old head applied, column exists) -> new head
# ═══════════════════════════════════════════════════════════════════════════
print("── 2. Already-migrated DB: d5e6f7a8b9c0 -> e7f8a9b0c1d2 SQL ──")
delta = alembic_sql(["upgrade", "d5e6f7a8b9c0:e7f8a9b0c1d2"])
drops2 = re.findall(r"(?i)drop\s+column\s+if\s+exists\s+stripe_customer_id", delta)
check("old-head DB upgrade emits exactly one DROP", len(drops2) == 1, f"{len(drops2)} drops")
adds2 = re.findall(r"(?i)add\s+column[^;]*stripe_customer_id[^;]*;", delta)
check("old-head DB upgrade emits NO ADD", not adds2, f"{adds2[:2]}")

# ═══════════════════════════════════════════════════════════════════════════
# 3. REVERSIBILITY — downgrade all the way back
# ═══════════════════════════════════════════════════════════════════════════
print("── 3. Reversibility: `alembic downgrade b4c5d6e7f8a9:base` SQL ──")
# Offline mode requires <fromrev>:<torev>; downgrade from the current head
# (user-data migration) all the way back to base.
down = alembic_sql(["downgrade", "b4c5d6e7f8a9:base"])
check("downgrade to base generated", len(down) > 50)
re_adds = re.findall(r"(?i)add\s+column\s+if\s+not\s+exists\s+stripe_customer_id", down)
check("downgrade re-adds column (documented reversibility contract)",
      len(re_adds) == 1, f"{len(re_adds)} re-adds")
email_drops = re.findall(r"(?i)drop\s+column\s+if\s+exists\s+email_verified", down)
check("downgrade drops email_verified (email-verification migration reverses)",
      len(email_drops) == 1, f"{len(email_drops)} drops")
admin_drops = re.findall(r"(?i)drop\s+column\s+if\s+exists\s+is_admin", down)
check("downgrade drops is_admin (admin-role migration reverses)",
      len(admin_drops) == 1, f"{len(admin_drops)} drops")
user_data_drops = re.findall(r"(?i)drop\s+table\s+if\s+exists\s+user_data", down)
check("downgrade drops user_data (head migration reverses)",
      len(user_data_drops) == 1, f"{len(user_data_drops)} drops")

# ═══════════════════════════════════════════════════════════════════════════
# 4. LIVE HTTP surface — auth still works after the Depends removal
# ═══════════════════════════════════════════════════════════════════════════
print("── 4. Live HTTP: auth surface after removing unused Depends import ──")
from unittest import mock
from tests.fakes import FakeUserDB, FakeLLMRouter
from fastapi.testclient import TestClient

mock.patch("src.database.PostgresDB", return_value=FakeUserDB()).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
from src.api import app

client = TestClient(app)
r = client.post("/auth/register", json={"email": "verify@stratoscan.app", "password": "hunter2hunter2"})
check("register → 200 + token", r.status_code == 200 and "access_token" in r.json(), f"{r.status_code}")
tok = r.json()["access_token"]
r = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
check("/auth/me → 200, plan=free", r.status_code == 200 and r.json()["plan"] == "free", f"{r.status_code}")
r = client.post("/backtest", json={"query": "tech stocks", "top_n": 2})
check("/backtest without token → 401 (auth gate intact)", r.status_code == 401, f"{r.status_code}")
r = client.post("/backtest", json={"query": "tech stocks", "top_n": 2},
                headers={"Authorization": f"Bearer {tok}"})
check("/backtest with token → NOT 401 (no paywall 402, no crash)",
      r.status_code in (200, 502, 503), f"{r.status_code}")

# ═══════════════════════════════════════════════════════════════════════════
print()
failed = [n for n, ok in checks if not ok]
print(f"VERDICT: {len(checks) - len(failed)}/{len(checks)} checks passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("🎯 All migration-behavior tests pass!")
