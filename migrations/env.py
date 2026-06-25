"""Alembic migration environment for the ClauseOps web platform.

This environment is wired to the application's async SQLAlchemy stack:

* the database URL comes from ``app.config.get_settings().database_url`` (the
  same environment-driven async PostgreSQL URL the app uses), and
* ``target_metadata`` is ``app.data.database.Base.metadata`` with
  ``app.data.models`` imported so every table is registered and visible to
  autogenerate.

Migrations run through the async engine via ``connection.run_sync`` so the
async ``postgresql+asyncpg`` driver works with Alembic's synchronous migration
API.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.engine import Connection
from sqlalchemy import pool

from alembic import context

# --- Application wiring -----------------------------------------------------
# Import the declarative Base and the settings, then import the models module
# for its side effect of registering every table on ``Base.metadata`` so
# autogenerate can see the full schema.
from app.config import get_settings
from app.data.database import Base
import app.data.models  # noqa: F401  (registers all ORM tables on Base.metadata)

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

# Inject the application's database URL at runtime (kept out of alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Configure Python logging from the alembic.ini config.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate' support.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure the context with a live connection and run the migrations."""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations within an async context."""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using the async engine."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
