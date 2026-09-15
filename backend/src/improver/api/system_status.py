from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.enums import ComponentHealthStatus
from improver.schemas import ComponentStatusRead, ComponentStatusWrite, SystemStatusRead
from improver.services.system_status import build_system_status, upsert_component_status

router = APIRouter(prefix="/system", tags=["system-status"])


@router.put("/components/{component_id}", response_model=ComponentStatusRead)
async def report_component_status(
    payload: ComponentStatusWrite,
    component_id: str = Path(pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> ComponentStatusRead:
    if component_id.startswith("source-") or component_id in {
        "worker-main",
        "ollama",
        "processing",
        "ollama-semaphore",
    }:
        raise HTTPException(status_code=409, detail="Component ID is reserved")
    observed_at = payload.observed_at or datetime.now(UTC)
    row = await upsert_component_status(
        session,
        component_id=component_id,
        label=payload.label,
        component_type=payload.component_type,
        status=payload.status,
        message=payload.message,
        metrics=payload.metrics,
        observed_at=observed_at,
        ttl_seconds=payload.ttl_seconds,
    )
    await session.commit()
    return ComponentStatusRead(
        id=row.id,
        label=row.label,
        component_type=row.component_type,
        status=ComponentHealthStatus(row.status),
        message=row.message,
        metrics=row.metrics,
        observed_at=row.observed_at,
        expires_at=row.expires_at,
    )


@router.get("/status", response_model=SystemStatusRead)
async def system_status(
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> SystemStatusRead:
    return await build_system_status(session, config)
