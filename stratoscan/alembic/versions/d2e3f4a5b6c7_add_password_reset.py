"""add password-reset token columns to users

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-26

Backs POST /auth/password-reset/request and /confirm. Only the SHA-256 of
the emailed token is stored (``reset_token_hash``), never the token itself,
so a read of this table yields nothing an attacker can use. One hash per
user: a second request overwrites the first, which is what makes "only the
most recent link works" true.

The runtime schema lives in ``src.database`` (``USERS_SCHEMA`` +
``USERS_PASSWORD_RESET_BACKFILL``, which heals a bare deploy); this revision
ports the same DDL into Alembic so managed deployments upgrade cleanly.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the hash + expiry and an index for the link-click lookup."""
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_hash TEXT;")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMPTZ;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_reset_token_hash "
        "ON users (reset_token_hash);"
    )


def downgrade() -> None:
    """Drop the index and both columns (reversibility contract)."""
    op.execute("DROP INDEX IF EXISTS idx_users_reset_token_hash;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS reset_token_expires;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS reset_token_hash;")
