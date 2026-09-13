from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import inspect, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from improver.config import get_config
from improver.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_config().database.resolved_url())
target_metadata = Base.metadata


def sync_application_schema_version(connection) -> None:
    if not inspect(connection).has_table("database_schema_version"):
        return
    revisions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if len(revisions) != 1 or not revisions[0].isdigit():
        raise RuntimeError(
            "Application schema version requires one numeric Alembic revision"
        )
    revision = revisions[0]
    connection.execute(
        text(
            "INSERT INTO database_schema_version (id, version, revision) "
            "VALUES (1, :version, :revision) "
            "ON CONFLICT (id) DO UPDATE SET "
            "version = EXCLUDED.version, revision = EXCLUDED.revision, updated_at = now()"
        ),
        {"version": int(revision), "revision": revision},
    )


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
        sync_application_schema_version(connection)


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
