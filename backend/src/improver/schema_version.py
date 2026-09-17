from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from sqlalchemy import text

MIN_DATABASE_SCHEMA_VERSION = 22
MIN_DATABASE_SCHEMA_REVISION = "0022"

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SchemaVersionState:
    version: int
    revision: str


def schema_is_compatible(
    current: SchemaVersionState | None,
    minimum: int = MIN_DATABASE_SCHEMA_VERSION,
) -> bool:
    return current is not None and current.version >= minimum


async def get_database_schema_version() -> SchemaVersionState | None:
    from improver.db import engine

    async with engine.connect() as connection:
        table_exists = await connection.scalar(
            text("SELECT to_regclass('public.database_schema_version')")
        )
        if table_exists is None:
            return None
        row = (
            await connection.execute(
                text(
                    "SELECT version, revision FROM database_schema_version "
                    "WHERE id = 1"
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return SchemaVersionState(version=int(row.version), revision=str(row.revision))


async def wait_for_compatible_database(
    minimum: int = MIN_DATABASE_SCHEMA_VERSION,
    poll_interval_seconds: float = 2.0,
) -> SchemaVersionState:
    last_status: tuple[object, ...] | None = None
    while True:
        try:
            current = await get_database_schema_version()
            if schema_is_compatible(current, minimum):
                assert current is not None
                log.info(
                    "database_schema_ready",
                    version=current.version,
                    revision=current.revision,
                    minimum=minimum,
                )
                return current
            status = (
                "waiting",
                current.version if current else None,
                current.revision if current else None,
            )
            if status != last_status:
                log.warning(
                    "database_schema_waiting",
                    current_version=current.version if current else None,
                    current_revision=current.revision if current else None,
                    minimum_version=minimum,
                    minimum_revision=MIN_DATABASE_SCHEMA_REVISION,
                )
                last_status = status
        except Exception as exc:  # database or version table may not be ready yet
            status = ("unavailable", type(exc).__name__)
            if status != last_status:
                log.warning(
                    "database_schema_unavailable",
                    error_type=type(exc).__name__,
                    minimum_version=minimum,
                )
                last_status = status
        await asyncio.sleep(poll_interval_seconds)
