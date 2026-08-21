"""create users table (auth)

Revision ID: c9e1f2a3b4c5
Revises: a1c2d3e4f506
Create Date: 2026-07-31

Creates the ``users`` table for Stratoscan's account system. Stores
app-level accounts with PBKDF2 password hashes and a ``plan`` column
('free' / 'premium' — kept for forward-compat; there is NO paywall,
Stratoscan is fully free). The premium gate for the backtesting engine
was removed along with the Stripe billing layer.

The schema mirrors ``src.database.USERS_SCHEMA`` as raw SQL DDL,
matching the project's migration style (no ORM metadata).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "a1c2d3e4f506"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the users table."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            BIGSERIAL PRIMARY KEY,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            plan          TEXT NOT NULL DEFAULT 'free',
            created_at    TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    """Drop the users table."""
    op.execute("DROP TABLE IF EXISTS users CASCADE;")
