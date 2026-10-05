"""explanations cache (Day 11: explain the result, don't just return it)

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-05

One plain-English reading per backtest result, keyed by the hash of the
whitelisted facts + prompt version, so the same result never gets two
different readings. Holds model prose over numbers only — no user data.
The runtime schema in ``src.database`` heals a bare deploy; this revision
ports the same DDL into Alembic.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS explanations (
            result_hash TEXT PRIMARY KEY,
            payload     JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS explanations;")
