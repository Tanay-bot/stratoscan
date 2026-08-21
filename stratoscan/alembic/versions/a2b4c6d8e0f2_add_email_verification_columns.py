"""add email verification columns to users

Revision ID: a2b4c6d8e0f2
Revises: f7a8b9c0d1e2
Create Date: 2026-08-01

Adds the columns that back Stratoscan's signup email verification
(``src/emails.py`` + the auth endpoints):

  - ``email_verified``             : bool, default False — set True when
                                     the user clicks the emailed link.
  - ``verification_token``         : one-time random token from the link.
  - ``verification_token_expires`` : TIMESTAMPTZ deadline for the token.

Fresh installs get these via ``src.database.USERS_SCHEMA`` (CREATE TABLE
IF NOT EXISTS); this migration upgrades EXISTING databases with
``ADD COLUMN IF NOT EXISTS`` (idempotent, no-op when already present).

The runtime backfill lives in ``src.database.USERS_EMAIL_VERIFICATION_BACKFILL``
(also executed by ``PostgresDB.init_schema``); this migration is the
Alembic-managed equivalent so ``alembic upgrade head`` upgrades cleanly.

Last step grandfathers pre-existing accounts: rows created before email
verification shipped have no ``verification_token``, so they are marked
``email_verified = TRUE`` — a future ``EMAIL_VERIFICATION_REQUIRED=true``
never locks out legacy users. New signups always carry a token
(unverified), so the UPDATE never touches them.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b4c6d8e0f2"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the email-verification columns + grandfather legacy users."""
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "email_verified BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token TEXT"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "verification_token_expires TIMESTAMPTZ"
    )
    # Grandfather pre-existing accounts (no token → verified). New signups
    # always carry a token, so they stay unverified until they click the link.
    op.execute(
        "UPDATE users SET email_verified = TRUE "
        "WHERE verification_token IS NULL AND email_verified = FALSE"
    )


def downgrade() -> None:
    """Remove the email-verification columns (reverse of upgrade)."""
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS verification_token")
    op.execute(
        "ALTER TABLE users DROP COLUMN IF EXISTS verification_token_expires"
    )
