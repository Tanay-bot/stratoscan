"""drop stripe_customer_id from users (paywall removed)

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-08-01

The Stripe paywall was removed — Stratoscan is fully free. This is the
head revision: it drops the now-dead ``stripe_customer_id`` column from
any database that already applied the original
``d5e6f7a8b9c0_add_stripe_customer_id`` migration. Fresh databases
never created the column (that migration is now a no-op), so the
``IF EXISTS`` guard makes this a safe no-op there too.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the dead stripe_customer_id column if it still exists."""
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS stripe_customer_id;")


def downgrade() -> None:
    """Re-add the column for reversibility (nullable, unique)."""
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT UNIQUE;"
    )
