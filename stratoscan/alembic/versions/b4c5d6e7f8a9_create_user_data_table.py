"""create user_data table (per-user saved watchlist/screeners/backtests)

Revision ID: b4c5d6e7f8a9
Revises: a2b4c6d8e0f2
Create Date: 2026-08-03

Adds the table that backs per-user saved state synced from the frontend
(``src/database.USER_DATA_SCHEMA`` / ``get_user_data`` / ``upsert_user_data``):

  - ``user_data``: one row per user (FK → users, ON DELETE CASCADE) with
    JSONB blobs for watchlist, saved_screeners and recent_backtests.

Previously this state was localStorage-only on the client. The API now
exposes GET/PUT /me/personal so a user's saved data follows them across
devices and is visible to the operator. The runtime schema lives in
``src.database``; this migration ports the same DDL into Alembic so
existing deployments upgrade cleanly with ``alembic upgrade head``.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "b3c5d7e9f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the user_data table (idempotent)."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_data (
            user_id          BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            watchlist        JSONB NOT NULL DEFAULT '[]'::jsonb,
            saved_screeners  JSONB NOT NULL DEFAULT '[]'::jsonb,
            recent_backtests JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at       TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    """Drop the user_data table (reversibility contract)."""
    op.execute("DROP TABLE IF EXISTS user_data;")
