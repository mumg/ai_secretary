from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.enums import AnalysisState, Direction, PrioritySource, TaskStatus
from improver.models import CommunicationEvent, CommunicationSource, Tag, Task
from improver.schemas import (
    ExternalTaskBatchRead,
    ExternalTaskBatchWrite,
    ExternalTaskSourceRead,
    ExternalTaskSourceWrite,
    ExternalTaskSyncItem,
    ExternalTaskWrite,
    TagReference,
)
from improver.services.calendar import BusinessCalendar
from improver.services.notifications import NotificationService
from improver.services.plans import rebuild_plan

router = APIRouter(prefix="/external-task-sources", tags=["external task sources"])

ACTIVE_TASK_STATUSES = {
    TaskStatus.NEEDS_CONFIRMATION,
    TaskStatus.NEW,
    TaskStatus.IN_PROGRESS,
    TaskStatus.POSSIBLY_COMPLETED,
}
SOURCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _validate_source_id(source_id: str) -> None:
    if not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise HTTPException(
            status_code=422,
            detail="source_id must contain only letters, digits, underscores, or hyphens",
        )


def _source_read(row: CommunicationSource) -> ExternalTaskSourceRead:
    return ExternalTaskSourceRead(
        id=row.id,
        label=row.label,
        enabled=row.enabled,
        tags=[TagReference(id=tag.id, name=tag.name) for tag in row.tags],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _content_hash(item: ExternalTaskWrite) -> str:
    payload = item.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _source_timestamp(event: CommunicationEvent) -> datetime | None:
    value = (event.raw_headers or {}).get("External-Task", {}).get("source_updated_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _apply_event(
    event: CommunicationEvent,
    item: ExternalTaskWrite,
    source: CommunicationSource,
    digest: str,
    now: datetime,
) -> None:
    event.subject = item.title
    event.body = item.description or item.title
    event.source_url = item.source_url
    event.occurred_at = item.occurred_at or event.occurred_at or now
    event.raw_headers = {
        "External-Task": {
            "source_updated_at": (
                item.source_updated_at.isoformat() if item.source_updated_at else None
            )
        }
    }
    event.content_hash = digest
    event.analysis_state = AnalysisState.SKIPPED
    event.analysis_result = {"external_task": True}
    event.analysis_error = None
    event.analysis_attempts = 0
    event.next_analysis_at = None
    event.semantic_summary = item.description or item.title
    event.semantic_categories = [source.label]
    event.semantic_keywords = []
    event.semantic_people = []
    event.semantic_organizations = []
    event.semantic_decisions = []
    event.semantic_agreements = []


def _apply_task(task: Task, item: ExternalTaskWrite, now: datetime) -> None:
    due_changed = task.due_at != item.due_at
    task.title = item.title
    task.description = item.description
    task.status = item.status
    task.priority = item.priority
    task.priority_source = PrioritySource.SOURCE
    task.due_at = item.due_at
    task.evidence = item.evidence
    task.confidence = 1.0
    task.manually_created = False
    task.completed_at = (
        item.source_updated_at or now if item.status == TaskStatus.COMPLETED else None
    )
    if due_changed:
        task.due_reminder_sent_at = None
        task.overdue_notification_date = None


@router.get("", response_model=list[ExternalTaskSourceRead])
async def list_external_task_sources(
    session: AsyncSession = Depends(get_session),
) -> list[ExternalTaskSourceRead]:
    rows = list(
        (
            await session.execute(
                select(CommunicationSource)
                .where(CommunicationSource.source_type == "external_tasks")
                .options(selectinload(CommunicationSource.tags))
                .order_by(CommunicationSource.label)
            )
        ).scalars()
    )
    return [_source_read(row) for row in rows]


@router.put("/{source_id}", response_model=ExternalTaskSourceRead)
async def put_external_task_source(
    source_id: str,
    payload: ExternalTaskSourceWrite,
    session: AsyncSession = Depends(get_session),
) -> ExternalTaskSourceRead:
    _validate_source_id(source_id)
    row = await session.get(CommunicationSource, source_id)
    if row is not None and row.source_type != "external_tasks":
        raise HTTPException(status_code=409, detail="Source ID belongs to another source type")
    unique_tag_ids = list(dict.fromkeys(payload.tag_ids))
    tags = list(
        (
            await session.execute(select(Tag).where(Tag.id.in_(unique_tag_ids)))
        ).scalars()
    )
    if len(tags) != len(unique_tag_ids):
        raise HTTPException(status_code=422, detail="One or more tags do not exist")
    if row is None:
        row = CommunicationSource(
            id=source_id,
            label=payload.label,
            source_type="external_tasks",
            enabled=payload.enabled,
            settings={},
        )
        session.add(row)
    else:
        row.label = payload.label
        row.enabled = payload.enabled
        row.last_error = None
    row.tags = tags
    await session.commit()
    await session.refresh(row)
    await session.refresh(row, attribute_names=["tags"])
    return _source_read(row)


@router.post("/{source_id}/tasks:batch", response_model=ExternalTaskBatchRead)
async def upsert_external_tasks(
    source_id: str,
    payload: ExternalTaskBatchWrite,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> ExternalTaskBatchRead:
    _validate_source_id(source_id)
    source = (
        await session.execute(
            select(CommunicationSource)
            .where(CommunicationSource.id == source_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if source is None or source.source_type != "external_tasks":
        raise HTTPException(status_code=404, detail="External task source not found")
    if not source.enabled:
        raise HTTPException(status_code=409, detail="External task source is disabled")

    external_ids = [item.external_id for item in payload.tasks]
    events_statement = (
        select(CommunicationEvent)
        .where(
            CommunicationEvent.source_id == source_id,
            CommunicationEvent.event_type == "external_task",
        )
        .options(selectinload(CommunicationEvent.tasks))
    )
    if not payload.close_missing:
        events_statement = events_statement.where(
            CommunicationEvent.external_id.in_(external_ids)
        )
    existing_events = list((await session.execute(events_statement)).scalars().unique())
    by_external_id = {event.external_id: event for event in existing_events}
    now = BusinessCalendar(config).now()
    created = updated = unchanged = closed_missing = 0
    changed = False
    notify: list[Task] = []
    result_items: list[ExternalTaskSyncItem] = []

    for item in payload.tasks:
        digest = _content_hash(item)
        event = by_external_id.get(item.external_id)
        task = event.tasks[0] if event and event.tasks else None
        stale = bool(
            event
            and item.source_updated_at
            and _source_timestamp(event)
            and item.source_updated_at < _source_timestamp(event)  # type: ignore[operator]
        )
        if event and task and (event.content_hash == digest or stale):
            action = "unchanged"
            unchanged += 1
        else:
            action = "updated" if event else "created"
            if event is None:
                event = CommunicationEvent(
                    source_id=source_id,
                    source_type="external_tasks",
                    external_id=item.external_id,
                    event_type="external_task",
                    direction=Direction.INTERNAL,
                    thread_external_id=item.external_id,
                    subject=item.title,
                    author=source.label,
                    participants=[],
                    occurred_at=item.occurred_at or now,
                    body=item.description or item.title,
                    source_url=item.source_url,
                    raw_headers={},
                    content_hash=digest,
                    analysis_state=AnalysisState.SKIPPED,
                )
                session.add(event)
                await session.flush()
                by_external_id[item.external_id] = event
            _apply_event(event, item, source, digest, now)
            if task is None:
                task = Task(source_event_id=event.id)
                session.add(task)
                notify.append(task)
            _apply_task(task, item, now)
            await session.flush()
            if action == "created":
                created += 1
            else:
                updated += 1
            changed = True
        assert task is not None
        result_items.append(
            ExternalTaskSyncItem(
                external_id=item.external_id,
                task_id=task.id,
                action=action,
                status=TaskStatus(task.status),
                active=TaskStatus(task.status) in ACTIVE_TASK_STATUSES,
            )
        )

    if payload.close_missing:
        present = set(external_ids)
        for event in existing_events:
            if event.external_id in present:
                continue
            for task in event.tasks:
                if TaskStatus(task.status) in ACTIVE_TASK_STATUSES:
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = None
                    closed_missing += 1
                    changed = True

    if changed:
        await rebuild_plan(session, now)
    await session.commit()

    notifier = NotificationService(config)
    for task in notify:
        if TaskStatus(task.status) not in ACTIVE_TASK_STATUSES:
            continue
        if task.status == TaskStatus.NEEDS_CONFIRMATION:
            notification_type = "TASK_CONFIRMATION_REQUIRED"
        elif task.priority == "CRITICAL":
            notification_type = "CRITICAL_TASK"
        else:
            notification_type = "NEW_TASK"
        await notifier.send(session, notification_type, str(task.id))

    return ExternalTaskBatchRead(
        source_id=source_id,
        created=created,
        updated=updated,
        unchanged=unchanged,
        closed_missing=closed_missing,
        items=result_items,
    )
