"""create job_runs and scheduler_heartbeat tables

Revision ID: f7a8b9c0d1e2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-01

Creates the tables that back Stratoscan's background job monitoring
(``src/job_monitor.py``):

  - ``job_runs``           : one row per job execution — status, timestamps,
                             duration, record count, error. Queried by
                             ``GET /ops/jobs``.
  - ``scheduler_heartbeat``: single-row (id=1) liveness beacon refreshed on
                             a throttled cadence so Neon can still
                             auto-suspend between refresh cycles.

The runtime schema lives in ``src.database.JOB_RUNS_SCHEMA`` /
``SCHEDULER_HEARTBEAT_SCHEMA``; this migration ports the same DDL into
Alembic so existing deployments upgrade cleanly with ``alembic upgrade
head``. All statements are idempotent (IF NOT EXISTS / ON CONFLICT).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the job-monitoring tables + indexes (idempotent)."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_runs (
            id          BIGSERIAL PRIMARY KEY,
            job_name    TEXT NOT NULL,
            status      TEXT NOT NULL,
            started_at  TIMESTAMPTZ DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            duration_ms DOUBLE PRECISION,
            records     INTEGER,
            details     TEXT,
            error       TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_runs_name_time "
        "ON job_runs(job_name, started_at DESC);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
            id           SMALLINT PRIMARY KEY DEFAULT 1,
            last_beat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            pid          INTEGER
        );
        """
    )


def downgrade() -> None:
    """Drop the job-monitoring tables (reversibility contract)."""
    op.execute("DROP TABLE IF EXISTS scheduler_heartbeat;")
    op.execute("DROP TABLE IF EXISTS job_runs CASCADE;")
