from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from improver.api.deps import get_runtime_config
from improver.config import AppConfig, SourceConfig
from improver.connectors import connector_for
from improver.db import get_session
from improver.models import (
    Attachment,
    CommunicationEvent,
    CommunicationSource,
    DailyPlan,
    Device,
    SourceCursor,
    SystemSetting,
    Tag,
    Task,
)
from improver.schemas import (
    AdminSettingsRead,
    AdminSettingsWrite,
    SourceRead,
    SourceWrite,
    TagRead,
    TagReference,
    TagWrite,
)
from improver.services.filter_reconciliation import reconcile_analysis_filters
from improver.services.identity_reconciliation import requeue_newly_eligible_events
from improver.services.settings import (
    SecretCipher,
    load_runtime_config,
    runtime_payload,
    save_runtime_settings,
    source_config_from_record,
)

router = APIRouter(prefix="/admin", tags=["admin"])
log = structlog.get_logger()

SOURCE_FIELDS = {
    "imap": {"host", "port", "tls", "username", "inbox_folder", "sent_folder"},
    "exchange": {
        "ews_url",
        "primary_smtp_address",
        "username",
        "auth_type",
        "inbox_folder",
        "sent_folder",
    },
    "mts_link": {"base_url", "poll_interval_seconds"},
    "external_tasks": set(),
}


def _source_read(row: CommunicationSource) -> SourceRead:
    return SourceRead(
        id=row.id,
        label=row.label,
        source_type=row.source_type,
        enabled=row.enabled,
        settings=row.settings,
        tags=[TagReference(id=tag.id, name=tag.name) for tag in row.tags],
        credential_configured=bool(row.credential_encrypted),
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _clean_source_settings(source_type: str, settings: dict) -> dict:
    allowed = SOURCE_FIELDS[source_type]
    return {key: value for key, value in settings.items() if key in allowed}


@router.get("/settings", response_model=AdminSettingsRead)
async def get_settings(
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> AdminSettingsRead:
    setting = await session.get(SystemSetting, 1)
    return AdminSettingsRead(
        settings=runtime_payload(config),
        firebase_configured=bool(setting and setting.firebase_credentials_encrypted),
    )


@router.put("/settings", response_model=AdminSettingsRead)
async def put_settings(
    payload: AdminSettingsWrite,
    session: AsyncSession = Depends(get_session),
) -> AdminSettingsRead:
    reconciliation: dict[str, int] | None = None
    identity_requeued: int | None = None
    try:
        previous_config = await load_runtime_config(session)
        setting = await save_runtime_settings(
            session,
            payload.settings,
            payload.firebase_credentials_json,
        )
        config = await load_runtime_config(session)
        if previous_config.analysis_filters != config.analysis_filters:
            stats = await reconcile_analysis_filters(session, config.analysis_filters)
            reconciliation = stats.model_dump()
            log.info("analysis_filters_reconciled", **reconciliation)
        if previous_config.identity.names != config.identity.names:
            identity_requeued = await requeue_newly_eligible_events(
                session, config.identity
            )
            log.info("identity_analysis_requeued", count=identity_requeued)
        await session.commit()
    except (ValueError, json.JSONDecodeError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return AdminSettingsRead(
        settings=runtime_payload(config),
        firebase_configured=bool(setting.firebase_credentials_encrypted),
        filter_reconciliation=reconciliation,
        identity_requeued=identity_requeued,
    )


@router.get("/sources", response_model=list[SourceRead])
async def list_sources(session: AsyncSession = Depends(get_session)) -> list[SourceRead]:
    rows = list(
        (
            await session.execute(
                select(CommunicationSource)
                .options(selectinload(CommunicationSource.tags))
                .order_by(CommunicationSource.label)
            )
        ).scalars()
    )
    return [_source_read(row) for row in rows]


async def _save_source(
    payload: SourceWrite,
    session: AsyncSession,
    existing: CommunicationSource | None,
) -> CommunicationSource:
    settings = _clean_source_settings(payload.source_type, payload.settings)
    credential = payload.credential or (
        SecretCipher().decrypt(existing.credential_encrypted)
        if existing and existing.credential_encrypted
        else None
    )
    try:
        SourceConfig.model_validate(
            {
                "id": payload.id,
                "type": payload.source_type,
                "enabled": payload.enabled,
                "credential": credential,
                **settings,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    reset_cursor = bool(
        existing
        and (
            existing.source_type != payload.source_type
            or existing.settings != settings
            or (not existing.enabled and payload.enabled)
        )
    )
    row = existing or CommunicationSource(id=payload.id)
    row.label = payload.label
    row.source_type = payload.source_type
    row.enabled = payload.enabled
    row.settings = settings
    row.last_error = None
    if payload.tag_ids is not None:
        unique_tag_ids = list(dict.fromkeys(payload.tag_ids))
        tags = list(
            (
                await session.execute(select(Tag).where(Tag.id.in_(unique_tag_ids)))
            ).scalars()
        )
        if len(tags) != len(unique_tag_ids):
            raise HTTPException(status_code=422, detail="Один или несколько тегов не существуют")
        row.tags = tags
    elif existing is None:
        row.tags = []
    if payload.credential:
        row.credential_encrypted = SecretCipher().encrypt(payload.credential)
    if existing is None:
        session.add(row)
    elif reset_cursor:
        await session.execute(
            sql_delete(SourceCursor).where(SourceCursor.source_id == payload.id)
        )
    await session.commit()
    await session.refresh(row)
    await session.refresh(row, attribute_names=["tags"])
    return row


@router.post("/sources", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceWrite,
    session: AsyncSession = Depends(get_session),
) -> SourceRead:
    if await session.get(CommunicationSource, payload.id):
        raise HTTPException(status_code=409, detail="Source ID already exists")
    return _source_read(await _save_source(payload, session, None))


@router.put("/sources/{source_id}", response_model=SourceRead)
async def update_source(
    source_id: str,
    payload: SourceWrite,
    session: AsyncSession = Depends(get_session),
) -> SourceRead:
    row = (
        await session.execute(
            select(CommunicationSource)
            .where(CommunicationSource.id == source_id)
            .options(selectinload(CommunicationSource.tags))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if payload.id != source_id:
        raise HTTPException(status_code=409, detail="Source ID cannot be changed")
    return _source_read(await _save_source(payload, session, row))


def _tag_read(row: Tag) -> TagRead:
    return TagRead(
        id=row.id,
        name=row.name,
        source_count=len(row.sources),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/tags", response_model=list[TagRead])
async def list_tags(session: AsyncSession = Depends(get_session)) -> list[TagRead]:
    rows = list(
        (
            await session.execute(
                select(Tag).options(selectinload(Tag.sources)).order_by(Tag.name)
            )
        ).scalars()
    )
    return [_tag_read(row) for row in rows]


@router.post("/tags", response_model=TagRead, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: TagWrite,
    session: AsyncSession = Depends(get_session),
) -> TagRead:
    normalized_name = payload.name.casefold()
    if await session.scalar(select(Tag.id).where(Tag.normalized_name == normalized_name)):
        raise HTTPException(status_code=409, detail="Тег с таким названием уже существует")
    row = Tag(name=payload.name, normalized_name=normalized_name)
    row.sources = []
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _tag_read(row)


@router.put("/tags/{tag_id}", response_model=TagRead)
async def update_tag(
    tag_id: uuid.UUID,
    payload: TagWrite,
    session: AsyncSession = Depends(get_session),
) -> TagRead:
    row = (
        await session.execute(
            select(Tag).where(Tag.id == tag_id).options(selectinload(Tag.sources))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Тег не найден")
    normalized_name = payload.name.casefold()
    duplicate = await session.scalar(
        select(Tag.id).where(Tag.normalized_name == normalized_name, Tag.id != tag_id)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Тег с таким названием уже существует")
    row.name = payload.name
    row.normalized_name = normalized_name
    await session.commit()
    await session.refresh(row)
    await session.refresh(row, attribute_names=["sources"])
    return _tag_read(row)


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await session.get(Tag, tag_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Тег не найден")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await session.get(CommunicationSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    attachment_paths = list(
        (
            await session.execute(
                select(Attachment.storage_path)
                .join(CommunicationEvent, Attachment.event_id == CommunicationEvent.id)
                .where(CommunicationEvent.source_id == source_id)
            )
        ).scalars()
    )
    await session.delete(row)
    await session.execute(sql_delete(DailyPlan))
    await session.commit()
    for storage_path in attachment_paths:
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError as exc:
            log.warning(
                "source_attachment_cleanup_failed",
                source_id=source_id,
                path=storage_path,
                error=str(exc),
            )
    return Response(status_code=204)


@router.post("/sources/{source_id}/test")
async def test_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> dict[str, str]:
    row = await session.get(CommunicationSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        result = await connector_for(source_config_from_record(row), config).test_connection()
        row.last_error = None
        await session.commit()
        return result
    except Exception as exc:
        row.last_error = str(exc)[:2000]
        await session.commit()
        raise HTTPException(status_code=502, detail="Connection failed") from None


@router.get("/status")
async def get_status(
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> dict:
    task_count = await session.scalar(select(func.count()).select_from(Task))
    device_count = await session.scalar(
        select(func.count()).select_from(Device).where(Device.active.is_(True))
    )
    source_count = await session.scalar(select(func.count()).select_from(CommunicationSource))
    ollama = "unavailable"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{config.llm.base_url}/api/version")
            response.raise_for_status()
            ollama = response.json().get("version", "ok")
    except httpx.HTTPError:
        pass
    return {
        "status": "ready",
        "time": datetime.now(UTC).isoformat(),
        "tasks": task_count or 0,
        "devices": device_count or 0,
        "sources": source_count or 0,
        "ollama": ollama,
    }
