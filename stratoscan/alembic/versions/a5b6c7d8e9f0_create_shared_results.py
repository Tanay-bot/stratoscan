"""create shared_results (Day 6: shareable proof)

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-04

A result becomes public only when its owner presses share. Each share gets a
fresh random 128-bit ``share_id``; revoking deletes the row, so an old link is
dead for good. ``summary`` is built server-side from the engine's own result
by a field whitelist, never supplied by a browser. Owner is an account
(``owner_user_id``, ON DELETE CASCADE: links die with the account) or an
anonymous trial session (``owner_anon``), claimed into the account at signup.

The runtime schema lives in ``src.database`` (``SHARED_RESULTS_SCHEMA`` +
``SHARED_RESULTS_INDEXES``, which heal a bare deploy); this revision ports the
same DDL into Alembic so managed deployments upgrade cleanly.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the table and its three lookup indexes (idempotent)."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_results (
            share_id      TEXT PRIMARY KEY,
            job_id        TEXT NOT NULL,
            owner_user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
            owner_anon    TEXT,
            query         TEXT NOT NULL,
            summary       JSONB NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_results_owner_user "
        "ON shared_results (owner_user_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_results_owner_anon "
        "ON shared_results (owner_anon);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_results_job ON shared_results (job_id);"
    )


def downgrade() -> None:
    """Drop the table (its indexes go with it)."""
    op.execute("DROP TABLE IF EXISTS shared_results;")
