"""create news tables (separate news database)

Revision ID: a1c2d3e4f506
Revises: b3e7ca01c230
Create Date: 2026-07-31

Creates the tables for Stratoscan's dedicated news database
(``NEWS_DATABASE_URL`` — a separate Neon project). Stores:
  - ``news_articles``   : individual articles + per-article LLM sentiment
  - ``stock_sentiment`` : aggregate sentiment per stock

The schema mirrors ``src.database.STOCK_SENTIMENT_SCHEMA`` /
``src.database.NEWS_ARTICLES_SCHEMA`` as raw SQL DDL, matching the
project's migration style (no ORM metadata).

NOTE: run this migration against the NEWS database, not the screener
database. Alembic's env.py reads ``DATABASE_URL`` by default; the news
DB's connection string is ``NEWS_DATABASE_URL`` in .env.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c2d3e4f506"
down_revision: Union[str, Sequence[str], None] = "b3e7ca01c230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the stock_sentiment and news_articles tables + indexes."""
    # ── stock_sentiment ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_sentiment (
            symbol             TEXT PRIMARY KEY,
            buzz               DOUBLE PRECISION,
            company_news_score DOUBLE PRECISION,
            sector_news_score  DOUBLE PRECISION,
            article_count      INTEGER DEFAULT 0,
            updated_at         TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )

    # ── news_articles ───────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS news_articles (
            id               BIGSERIAL PRIMARY KEY,
            symbol           TEXT NOT NULL,
            headline         TEXT NOT NULL,
            summary          TEXT,
            source           TEXT,
            url              TEXT,
            published_at     TIMESTAMPTZ,
            sentiment        TEXT,
            sentiment_score  DOUBLE PRECISION,
            fetched_at       TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(symbol, url)
        );
        """
    )

    # ── Indexes ─────────────────────────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_news_symbol_time "
        "ON news_articles(symbol, published_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_news_fetched "
        "ON news_articles(fetched_at DESC);"
    )


def downgrade() -> None:
    """Drop the news tables."""
    op.execute("DROP TABLE IF EXISTS news_articles CASCADE;")
    op.execute("DROP TABLE IF EXISTS stock_sentiment CASCADE;")
