from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from improver import __version__
from improver.api import (
    admin,
    chat,
    devices,
    events,
    external_tasks,
    meeting_results,
    meetings,
    mts_link_auth,
    plans,
    realtime,
    system_status,
    tasks,
    threads,
    ui,
)
from improver.config import get_config
from improver.db import engine
from improver.schema_version import get_database_schema_version, wait_for_compatible_database
from improver.services.realtime import hub
from improver.services.updates import update_checker


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
    listener = asyncio.create_task(hub.run())
    updates = asyncio.create_task(update_checker.run())
    try:
        yield
    finally:
        listener.cancel()
        updates.cancel()
        with suppress(asyncio.CancelledError):
            await listener
        with suppress(asyncio.CancelledError):
            await updates
        await engine.dispose()


app = FastAPI(title="Improver API", version=__version__, lifespan=lifespan)
if get_config().server.local_web_only:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


@app.middleware("http")
async def reject_cross_origin_writes(request: Request, call_next):
    """Browsers attach mTLS credentials automatically; reject cross-site form writes.

    Native clients do not send Origin/Fetch Metadata and retain their existing contract.
    Caddy terminates TLS, so compare authority rather than the internal HTTP scheme.
    """
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        cross_site = request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
        if origin:
            try:
                parsed = urlsplit(origin)
                cross_site |= (
                    parsed.scheme not in {"http", "https"}
                    or parsed.netloc.lower() != request.headers.get("host", "").lower()
                )
            except ValueError:
                cross_site = True
        if cross_site:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin write denied"})
    return await call_next(request)


app.include_router(tasks.router, prefix="/api/v1")
app.include_router(plans.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(external_tasks.router, prefix="/api/v1")
app.include_router(devices.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(mts_link_auth.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(threads.router, prefix="/api/v1")
app.include_router(meetings.router, prefix="/api/v1")
app.include_router(meeting_results.router, prefix="/api/v1")
app.include_router(system_status.router, prefix="/api/v1")
app.include_router(ui.router, prefix="/api/v1")
app.include_router(realtime.router, prefix="/api/v1")

web_dir = Path(__file__).parent / "web"
app.mount("/admin/assets", StaticFiles(directory=web_dir / "assets"), name="admin-assets")
app.mount("/app/assets", StaticFiles(directory=web_dir / "assets"), name="app-assets")


@app.get("/", include_in_schema=False)
@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
async def user_ui() -> FileResponse:
    return FileResponse(web_dir / "app.html", headers={"Cache-Control": "no-cache"})


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_ui() -> FileResponse:
    return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})


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
