"""entitlements (Day 12: tiers without a payment provider)

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-05

- ``users.plan_pending`` / ``plan_change_at`` / ``plan_change_reason``: a
  period-end downgrade or a grace-period revocation waiting to apply.
- ``users.grandfathered_pro``: the first 100 accounts are Pro for life. This
  revision grants it to the first 100 EXISTING accounts (by signup order) and
  records how many slots it issued in ``app_counters``, so signups after it
  only receive what is left.
- ``usage_counters``: metered usage per account per calendar month.
- The old tier name ``premium`` becomes ``pro``.

The runtime schema in ``src.database`` heals a bare deploy; this revision
ports the same DDL into Alembic.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GRANDFATHER_LIMIT = 100  # src.entitlements.GRANDFATHER_LIMIT


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_pending TEXT;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_change_at TIMESTAMPTZ;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_change_reason TEXT;")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS grandfathered_pro BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    op.execute("UPDATE users SET plan = 'pro' WHERE plan = 'premium';")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_counters (
            name  TEXT PRIMARY KEY,
            value BIGINT NOT NULL DEFAULT 0
        );
        """
    )
    op.execute(
        f"""
        UPDATE users SET grandfathered_pro = TRUE
        WHERE id IN (SELECT id FROM users ORDER BY created_at NULLS LAST, id LIMIT {GRANDFATHER_LIMIT});
        """
    )
    op.execute(
        """
        INSERT INTO app_counters (name, value)
        SELECT 'grandfathered_pro', COUNT(*) FROM users WHERE grandfathered_pro
        ON CONFLICT (name) DO UPDATE SET value = GREATEST(app_counters.value, EXCLUDED.value);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_counters (
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            period  TEXT NOT NULL,
            kind    TEXT NOT NULL,
            count   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, period, kind)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS usage_counters;")
    op.execute("DROP TABLE IF EXISTS app_counters;")
    op.execute("UPDATE users SET plan = 'premium' WHERE plan = 'pro';")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS grandfathered_pro;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS plan_change_reason;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS plan_change_at;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS plan_pending;")
