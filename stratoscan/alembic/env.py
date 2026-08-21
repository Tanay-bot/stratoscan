"""Alembic environment configuration for Stratoscan.

Reads DATABASE_URL from the project's .env file so migrations
use the same database as the application — no duplicate config.

Stratoscans schema is defined as raw SQL DDL in ``src.database``,
not through SQLAlchemy ORM models. Migrations use ``op.execute()``
to run SQL directly, so ``target_metadata`` is left empty.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv

# ── Allow imports from the project root ──────────────────────────────
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

# ── Load DATABASE_URL from .env ──────────────────────────────────────
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

database_url = os.environ.get("DATABASE_URL", "")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and fill in your "
        "PostgreSQL connection string."
    )

# ── Alembic Config ───────────────────────────────────────────────────
config = context.config

# Override the ini-file URL with our DATABASE_URL from .env
config.set_main_option("sqlalchemy.url", database_url)

# Set up Python loggers from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target Metadata ──────────────────────────────────────────────────
# Stratoscan uses raw SQL (op.execute()) — no ORM metadata for autogenerate.
# To create a new migration: `alembic revision -m "description"` and write
# the upgrade/downgrade SQL manually.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without connecting)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to live DB)."""
    from sqlalchemy import engine_from_config, pool

    # Same cold-start budget as the app's pools: Neon suspends idle
    # databases, and a wake-up can take >15s. Without a connect timeout the
    # docker entrypoint's `alembic upgrade head` can fail on first boot.
    connect_args = {"connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "15"))}
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
