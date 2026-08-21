"""Hermetic migration-chain regression tests — no DB connection needed.

Run: python -m tests.test_migrations

Guards the paywall removal: the Alembic chain must never re-create the
deleted ``stripe_customer_id`` column (fresh DBs), the chain must stay
linear with exactly one head, and the runtime schema (``USERS_SCHEMA``)
must match. All checks are static file reads — nothing connects to a
database, so this is safe in CI.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database import USERS_SCHEMA

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _migrations() -> list[tuple[str, str, str | None]]:
    """Return (filename, revision, down_revision) for every migration."""
    out: list[tuple[str, str, str | None]] = []
    for f in sorted(_VERSIONS.glob("*.py")):
        if f.name.startswith("__"):
            continue
        src = f.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', src, re.M)
        down = re.search(
            r'^down_revision: Union\[str, Sequence\[str\], None\] = "?([^"\n]*)"?',
            src,
            re.M,
        )
        down_raw = down.group(1) if down else None
        # A literal ``None`` on the RHS (e.g. ``= None`` for the base
        # revision) parses as the string "None" — normalize it to null.
        down_val = None if not down_raw or down_raw == "None" else down_raw
        out.append((f.name, rev.group(1) if rev else "", down_val))
    return out


print("1. Migration chain is linear with a single head...")
migs = _migrations()
assert migs, "no migration files found"
revs = [r for _, r, _ in migs]
downs = [d for _, _, d in migs]
bases = [d for d in downs if not d]
heads = [r for r in revs if r not in [d for d in downs if d]]
assert len(bases) == 1, f"expected exactly 1 base revision, got {bases}"
assert len(heads) == 1, f"expected exactly 1 head, got {heads}"
assert all(d in revs for d in downs if d), "dangling down_revision"
assert len(revs) == len(set(revs)), "duplicate revision ids"
print(f"✅ chain linear: base={bases[0]} head={heads[0]} ({len(revs)} migrations)")

print("2. No migration's UPGRADE path re-creates stripe_customer_id...")
# Scope the check to the upgrade() body only: a reversible downgrade() may
# legitimately re-add the column (rollback), but fresh DBs run only the
# upgrade path, so that path must never CREATE the dead paywall column.
# Match executable SQL statements (up to a semicolon) that mention the
# column — docstring prose referencing it (e.g. "...is now a no-op") is
# fine; an executed ``ADD COLUMN`` is not. The head migration's DROP is
# the one legitimate touch.
for name, _, _ in migs:
    src = (_VERSIONS / name).read_text(encoding="utf-8")
    upgrade_body = src.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    for stmt in re.finditer(r"[^;]*stripe_customer_id[^;]*;", upgrade_body):
        sql = stmt.group(0)
        assert "ADD COLUMN" not in sql.upper(), (
            f"{name} upgrade() ADDs stripe_customer_id: {sql.strip()!r} — "
            "fresh DBs would recreate the dead paywall column"
        )
    # Defense-in-depth: also catch Alembic's column API (``op.add_column``),
    # which has no semicolon-terminated SQL string. The project uses raw
    # ``op.execute`` today, but a future dev could add the column this way.
    add_cols = re.findall(
        r"op\.add_column\s*\([^)]*stripe_customer_id", upgrade_body
    )
    assert not add_cols, (
        f"{name} upgrade() uses op.add_column for stripe_customer_id: "
        f"{add_cols[:2]!r} — fresh DBs would recreate the dead paywall column"
    )
print("✅ no upgrade() executes ADD COLUMN stripe_customer_id")

print("3. USERS_SCHEMA (runtime source of truth) has no stripe_customer_id...")
assert "stripe_customer_id" not in USERS_SCHEMA, "USERS_SCHEMA still has stripe_customer_id"
print("✅ USERS_SCHEMA clean")

print()
print("🎯 All migration tests pass!")
