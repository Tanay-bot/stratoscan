"""create events + error_log tables

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-29

Day 4 instrumentation. Two tables, one revision, because they are one concern:
what users DO (``events``, written by ``src/analytics.track``) and what breaks
(``error_log``, written by ``src/api.py``'s generic exception handler).

``events.user_id`` is ``REFERENCES users(id) ON DELETE SET NULL`` deliberately:
deleting an account anonymises its events instead of deleting them. The rows
carry no personal data once detached from the user, and removing them would
rewrite history — yesterday's funnel count would change because someone left
today.

The runtime schema lives in ``src.database`` (``EVENTS_SCHEMA`` /
``ERROR_LOG_SCHEMA`` + their index lists, wired into
``PostgresDB.init_schema()``); this revision ports the same DDL into Alembic so
managed deployments upgrade cleanly. Both, not either — the same
belt-and-suspenders contract as every table before it (see CLAUDE.md §18).

Note the ordering inside ``upgrade()``: ``events`` references ``users``, so on a
database where users has never existed the chain must already have created it —
which ``c9e1f2a3b4c5_create_users_table`` does, earlier in the chain.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the product-event and error-log tables + their indexes."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id          BIGSERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            anon_id     TEXT,
            user_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
            props       JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_name_time "
        "ON events (name, occurred_at DESC);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_events_anon ON events (anon_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS error_log (
            id          BIGSERIAL PRIMARY KEY,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            method      TEXT,
            route       TEXT,
            error_type  TEXT,
            trace       TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_error_log_time "
        "ON error_log (occurred_at DESC);"
    )


def downgrade() -> None:
    """Drop both tables (reversibility contract)."""
    op.execute("DROP TABLE IF EXISTS error_log;")
    op.execute("DROP INDEX IF EXISTS idx_events_anon;")
    op.execute("DROP INDEX IF EXISTS idx_events_user;")
    op.execute("DROP INDEX IF EXISTS idx_events_name_time;")
    op.execute("DROP TABLE IF EXISTS events;")
