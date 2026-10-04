"""saved strategies + weekly digest (Day 10: a reason to come back)

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-04

``saved_strategies`` holds server-built snapshots of a finished backtest (the
query, the exact rules the engine ran, the candidate symbols); the weekly
digest re-runs them. ``digest_sends`` is the per-(user, ISO week) double-send
guard. ``users.weekly_digest`` is the opt-out. Both tables cascade with the
account. The runtime schema in ``src.database`` heals a bare deploy; this
revision ports the same DDL into Alembic.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS weekly_digest BOOLEAN NOT NULL DEFAULT TRUE;"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_strategies (
            id               BIGSERIAL PRIMARY KEY,
            user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            query            TEXT NOT NULL,
            rules            JSONB NOT NULL,
            symbols          JSONB NOT NULL DEFAULT '[]'::jsonb,
            saved_return_pct DOUBLE PRECISION,
            saved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_saved_strategies_user ON saved_strategies (user_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS digest_sends (
            user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            week_key   TEXT NOT NULL,
            status     TEXT NOT NULL,
            attempts   INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, week_key)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS digest_sends;")
    op.execute("DROP TABLE IF EXISTS saved_strategies;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS weekly_digest;")
