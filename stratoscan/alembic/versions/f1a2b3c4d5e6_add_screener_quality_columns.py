"""add screener quality columns to stock_master

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-08-01 12:00:00.000000

Tier 2 screener realism: unlock valuation ratios (P/S, P/B, EV/EBITDA),
profitability (ROE, ROA, gross/net margin), growth (revenue/EPS YoY) and
extra technicals (MACD, ATR-14, Bollinger bands) as screenable filters.
All columns are nullable — a NULL simply means \"no data for this name\",
and the screener's filters naturally exclude rows without the field. The
runtime schema is kept in sync in ``src.database.STOCK_MASTER_SCHEMA``;
this migration ports the delta into Alembic so existing deployments
upgrade cleanly with ``alembic upgrade head``.

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable screener-quality columns (idempotent)."""
    for column in (
        "ps_ratio DOUBLE PRECISION",
        "pb_ratio DOUBLE PRECISION",
        "ev_ebitda DOUBLE PRECISION",
        "roe_ttm DOUBLE PRECISION",
        "roa_ttm DOUBLE PRECISION",
        "gross_margin_ttm DOUBLE PRECISION",
        "net_margin_ttm DOUBLE PRECISION",
        "revenue_growth_ttm DOUBLE PRECISION",
        "eps_growth_ttm DOUBLE PRECISION",
        "macd_12_26 DOUBLE PRECISION",
        "atr_14 DOUBLE PRECISION",
        "bollinger_upper DOUBLE PRECISION",
        "bollinger_lower DOUBLE PRECISION",
    ):
        op.execute(
            f"ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS {column};"
        )

    # Help the screener's most-used new filters
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_avg_volume ON stock_master(avg_volume);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_sma_50 ON stock_master(sma_50);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sma_200 ON stock_master(sma_200);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_eps_ttm ON stock_master(eps_ttm);")


def downgrade() -> None:
    """Drop the added columns (reversibility contract)."""
    for column in (
        "ps_ratio", "pb_ratio", "ev_ebitda", "roe_ttm", "roa_ttm",
        "gross_margin_ttm", "net_margin_ttm", "revenue_growth_ttm",
        "eps_growth_ttm", "macd_12_26", "atr_14",
        "bollinger_upper", "bollinger_lower",
    ):
        op.execute(f"ALTER TABLE stock_master DROP COLUMN IF EXISTS {column};")
