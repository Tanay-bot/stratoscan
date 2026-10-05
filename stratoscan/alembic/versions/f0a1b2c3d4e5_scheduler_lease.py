"""scheduler_heartbeat.owner (Day 15: exactly one scheduler, enforced)

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-10-05

``owner`` ("host:pid") turns the liveness beacon into a lease: a scheduler
claims it before running any job, and a second one is refused while the
first's beat is fresh (src/job_monitor.py, claim_scheduler).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE scheduler_heartbeat ADD COLUMN IF NOT EXISTS owner TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE scheduler_heartbeat DROP COLUMN IF EXISTS owner;")
