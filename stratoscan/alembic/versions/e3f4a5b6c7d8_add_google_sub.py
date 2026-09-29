"""add google_sub to users

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-29

Backs "Continue with Google" (GET /auth/google/start + /callback, logic in
``src/google_oauth.py``). ``google_sub`` is Google's subject id — stable for
the life of the account, unlike the email, so it is what identifies a
returning sign-in. Unique, so one Google identity can never map to two rows;
Postgres allows any number of NULLs in a unique index, so email/password
accounts are unaffected.

The runtime schema lives in ``src.database`` (``USERS_SCHEMA`` +
``USERS_GOOGLE_SUB_BACKFILL``, which heals a bare deploy); this revision ports
the same DDL into Alembic so managed deployments upgrade cleanly. Both, not
either — the same belt-and-suspenders contract as the token_version and
password-reset columns before it (see CLAUDE.md §18).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the column and a unique index for the sign-in lookup."""
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub TEXT;")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub "
        "ON users (google_sub);"
    )


def downgrade() -> None:
    """Drop the index and the column (reversibility contract)."""
    op.execute("DROP INDEX IF EXISTS idx_users_google_sub;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS google_sub;")
