"""create stock_master table

Revision ID: b3e7ca01c230
Revises:
Create Date: 2026-07-30 19:37:11.611312

Stratoscans primary stock universe table: ~5,000 US stocks with
fundamental and technical indicators. The schema is defined as raw SQL
in ``src.database.STOCK_MASTER_SCHEMA``; this migration ports those
DDL statements into Alembic so schema changes are tracked through
migrations going forward.

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e7ca01c230"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the stock_master table and supporting indexes."""
    # ── Table ──────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_master (
            symbol          TEXT PRIMARY KEY,
            company_name    TEXT NOT NULL,
            sector          TEXT,
            industry        TEXT,
            market_cap      BIGINT,
            current_price   DOUBLE PRECISION,
            price_change_pct DOUBLE PRECISION,
            volume          BIGINT,
            avg_volume      BIGINT,
            pe_ratio        DOUBLE PRECISION,
            eps_ttm         DOUBLE PRECISION,
            dividend_yield  DOUBLE PRECISION,
            beta            DOUBLE PRECISION,
            rsi_14          DOUBLE PRECISION,
            sma_50          DOUBLE PRECISION,
            sma_200         DOUBLE PRECISION,
            high_52w        DOUBLE PRECISION,
            low_52w         DOUBLE PRECISION,
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )

    # ── Indexes ────────────────────────────────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS idx_sector ON stock_master(sector);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_market_cap ON stock_master(market_cap);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pe_ratio ON stock_master(pe_ratio);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rsi ON stock_master(rsi_14);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_volume ON stock_master(volume);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_industry ON stock_master(industry);")


def downgrade() -> None:
    """Drop the stock_master table and all its indexes."""
    op.execute("DROP TABLE IF EXISTS stock_master CASCADE;")
