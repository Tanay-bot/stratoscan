"""add stripe_customer_id to users (real paywall) — SUPERSEDED

Revision ID: d5e6f7a8b9c0
Revises: c9e1f2a3b4c5
Create Date: 2026-07-31

**This migration is now a no-op.** The Stripe paywall was removed
(2026-08) — Stratoscan is fully free. Fresh databases must never create
the dead ``stripe_customer_id`` column, so the ``upgrade()`` that added
it is neutralized. The column is actually dropped from any database that
already ran the original version of this migration by the successor
revision (``e7f8a9b0c1d2_drop_stripe_customer_id.py``).

Keeping the revision id intact is deliberate: an already-migrated
database records ``d5e6f7a8b9c0`` in ``alembic_version``, and deleting or
renaming this file would break its chain.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c9e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op — the paywall was removed; the column must never be created.

    Fresh databases skip creating ``stripe_customer_id`` entirely. The
    successor revision drops it from databases that applied the original
    (pre-removal) version of this migration.
    """
    # Intentionally empty. Do not re-add the column here.


def downgrade() -> None:
    """Intentionally a no-op as well — the feature is gone.

    Re-adding a column for a deleted feature would only resurrect dead
    weight; the schema no longer references it anywhere.
    """
    # Intentionally empty.
