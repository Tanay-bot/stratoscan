"""stock_master.asset_type (Day 14: a curated, labelled ETF set)

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-05

Every existing row is a stock (the universe came from Finnhub's "Common
Stock" listing), so the column defaults to 'stock'. The fund rows themselves
are seeded by ``PostgresDB.seed_etfs()`` from ``src/etfs.py`` (it runs from
``init_schema`` at API start and ``python -m src.main setup``) — kept out of
the migration so the list has one home.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS asset_type TEXT NOT NULL DEFAULT 'stock';"
    )


def downgrade() -> None:
    op.execute("DELETE FROM stock_master WHERE asset_type = 'etf';")
    op.execute("ALTER TABLE stock_master DROP COLUMN IF EXISTS asset_type;")
