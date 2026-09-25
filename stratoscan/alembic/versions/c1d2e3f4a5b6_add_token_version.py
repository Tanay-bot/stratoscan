"""add token_version to users (session revocation counter)

Revision ID: c1d2e3f4a5b6
Revises: b4c5d6e7f8a9
Create Date: 2026-09-25

Adds the integer that makes a JWT revocable. ``create_access_token`` stamps
it as the ``ver`` claim, ``get_current_user`` rejects any token whose claim
doesn't match the row, and POST /auth/logout-all increments it — so a bump
invalidates every token the account ever issued, with no token blocklist and
no extra query (the user row is already loaded on each authed request).

The runtime schema lives in ``src.database`` (``USERS_SCHEMA`` +
``USERS_TOKEN_VERSION_BACKFILL``, which heals a bare deploy); this revision
ports the same DDL into Alembic so managed deployments upgrade cleanly with
``alembic upgrade head``.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the counter (idempotent — every existing row starts at 0)."""
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "token_version INTEGER NOT NULL DEFAULT 0;"
    )


def downgrade() -> None:
    """Drop the counter (reversibility contract)."""
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS token_version;")
