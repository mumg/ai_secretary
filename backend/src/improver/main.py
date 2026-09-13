from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from improver import __version__
from improver.api import (
    admin,
    chat,
    devices,
    events,
    meeting_results,
    meetings,
    plans,
    tasks,
    threads,
)
from improver.config import get_config
from improver.db import engine
from improver.schema_version import get_database_schema_version, wait_for_compatible_database


def configure_logging() -> None:
    config = get_config()
    logging.basicConfig(level=getattr(logging, config.server.log_level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    await wait_for_compatible_database()
    yield
    await engine.dispose()


app = FastAPI(title="Improver API", version=__version__, lifespan=lifespan)
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(plans.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(devices.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(threads.router, prefix="/api/v1")
app.include_router(meetings.router, prefix="/api/v1")
app.include_router(meeting_results.router, prefix="/api/v1")

web_dir = Path(__file__).parent / "web"
app.mount("/admin/assets", StaticFiles(directory=web_dir / "assets"), name="admin-assets")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_ui() -> FileResponse:
    return FileResponse(web_dir / "index.html")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
async def ready() -> dict[str, str | int]:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    schema = await get_database_schema_version()
    return {
        "status": "ready",
        "database_schema_version": schema.version if schema else 0,
        "database_schema_revision": schema.revision if schema else "unknown",
    }
