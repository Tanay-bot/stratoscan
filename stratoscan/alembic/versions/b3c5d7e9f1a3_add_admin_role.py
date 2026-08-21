"""add admin role column to users

Revision ID: b3c5d7e9f1a3
Revises: a2b4c6d8e0f2
Create Date: 2026-08-01

Adds ``is_admin`` to the users table — the flag that gates the admin
dashboard (``/admin`` + the ``/admin/*`` management API). Fresh installs
get it via ``src.database.USERS_SCHEMA`` (CREATE TABLE IF NOT EXISTS);
this migration upgrades EXISTING databases with ``ADD COLUMN IF NOT
EXISTS`` (idempotent, no-op when already present).

The runtime backfill lives in ``src.database.USERS_EMAIL_VERIFICATION_BACKFILL``
(which now also carries the admin column, executed by
``PostgresDB.init_schema``); this migration is the Alembic-managed
equivalent so ``alembic upgrade head`` upgrades cleanly.

Admin bootstrap: no rows are promoted here — the role is granted via the
``ADMIN_EMAILS`` env var at login (see src/config.py + the /auth/login
endpoint), or manually via PATCH /admin/users/{id}.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c5d7e9f1a3"
down_revision: Union[str, Sequence[str], None] = "a2b4c6d8e0f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the admin-role column (idempotent)."""
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "is_admin BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    """Remove the admin-role column (reverse of upgrade)."""
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_admin")
