"""Alembic environment (async).

URL comes from autora.infra.settings (env / .env), never from alembic.ini.
Every table must be on Base.metadata for autogenerate / ``alembic check``. Domain tables live
in their domain (``autora/domains/<name>/models.py``), so the list comes from the composition
root's ``load_models`` (the one import from db upward that .importlinter allows).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from autora.app import load_models
from autora.db.base import Base
from autora.infra.settings import get_settings

load_models()
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.get_main_option("sqlalchemy.url")
    return override or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.")
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
