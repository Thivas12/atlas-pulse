"""Async Alembic environment configured from AtlasPulse settings."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from atlas_pulse.config import get_settings

configuration = context.config
if configuration.config_file_name is not None:
    fileConfig(configuration.config_file_name)

configuration.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = None


def run_migrations_offline() -> None:
    """Generate SQL without opening a database connection."""
    context.configure(
        url=configuration.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: object) -> None:
    """Run configured migrations on an existing synchronous facade."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations using SQLAlchemy's asyncpg engine."""
    engine = async_engine_from_config(
        configuration.get_section(configuration.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(run_sync_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
