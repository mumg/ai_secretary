from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.enums import PrioritySource, TaskStatus
from improver.models import CommunicationSource, Reminder, Task
from improver.schemas import (
    DictatedTaskCreate,
    ReminderCreate,
    ReminderRead,
    TaskCreate,
    TaskDetailRead,
    TaskRead,
    TaskSourceRead,
    TaskUpdate,
)
from improver.services.calendar import BusinessCalendar
from improver.services.full_text_search import full_text_match, normalize_search_query
from improver.services.notifications import NotificationService
from improver.services.ollama import OllamaAnalyzer
from improver.services.plans import rebuild_plan, rerank_active_tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _get_task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    result = await session.execute(
        select(Task).where(Task.id == task_id).options(selectinload(Task.reminders))
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    include_closed: bool = Query(False),
    order: str = Query("rank", pattern="^(rank|due)$"),
    query: str | None = Query(None, alias="q", max_length=200),
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> list[Task]:
    calendar = BusinessCalendar(config)
    await rerank_active_tasks(session, calendar.now())
    conditions = []
    if not include_closed:
        conditions.append(Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.CANCELLED]))
    normalized_query = normalize_search_query(query)
    if normalized_query:
        conditions.append(full_text_match("tasks", normalized_query))
    statement = select(Task).where(*conditions).options(selectinload(Task.reminders))
    if order == "due":
        priority_order = case(
            (Task.priority == "CRITICAL", 4),
            (Task.priority == "HIGH", 3),
            (Task.priority == "NORMAL", 2),
            (Task.priority == "LOW", 1),
            else_=0,
        )
        statement = statement.order_by(priority_order.desc(), Task.due_at.asc().nulls_last())
    else:
        statement = statement.order_by(Task.ranking_score.desc(), Task.due_at.asc().nulls_last())
    result = await session.execute(statement)
    await session.commit()
    return list(result.scalars().unique())


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> Task:
    calendar = BusinessCalendar(config)
    task = Task(
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        priority_source=PrioritySource.MANUAL,
        due_at=calendar.normalize_due(payload.due_at),
        manually_created=True,
    )
    session.add(task)
    await session.flush()
    await rebuild_plan(session, calendar.now())
    await session.commit()
    if payload.priority.value == "CRITICAL":
        await NotificationService(config).send(session, "CRITICAL_TASK", str(task.id))
    return await _get_task(session, task.id)


@router.post("/from-text", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task_from_text(
    payload: DictatedTaskCreate,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> Task:
    calendar = BusinessCalendar(config)
    now = calendar.now()
    formalized = await OllamaAnalyzer(config).formalize_task(
        text=payload.text,
        now=now,
        timezone_name=config.server.timezone,
    )
    task = Task(
        title=" ".join(formalized.title.split()),
        description=formalized.description,
        priority=formalized.priority,
        priority_source=PrioritySource.LLM,
        due_at=calendar.normalize_due(formalized.due_at),
        evidence=payload.text,
        confidence=1.0,
        manually_created=True,
    )
    session.add(task)
    await session.flush()
    await rebuild_plan(session, now)
    await session.commit()
    if formalized.priority.value == "CRITICAL":
        await NotificationService(config).send(session, "CRITICAL_TASK", str(task.id))
    return await _get_task(session, task.id)


@router.get("/{task_id}", response_model=TaskDetailRead)
async def get_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> TaskDetailRead:
    task = await _get_task(session, task_id)
    if task.source_event_id is None:
        return TaskDetailRead(task=TaskRead.model_validate(task), source=None)

    await session.refresh(task, attribute_names=["source_event"])
    event = task.source_event
    if event is None:
        return TaskDetailRead(task=TaskRead.model_validate(task), source=None)

    source_record = await session.get(CommunicationSource, event.source_id)
    source = TaskSourceRead(
        id=event.id,
        source_id=event.source_id,
        source_label=source_record.label if source_record else event.source_id,
        source_type=event.source_type,
        event_type=event.event_type,
        direction=event.direction,
        subject=event.subject,
        author=event.author,
        participants=event.participants,
        occurred_at=event.occurred_at,
        body=event.body,
        source_url=event.source_url,
    )
    return TaskDetailRead(task=TaskRead.model_validate(task), source=source)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> Task:
    task = await _get_task(session, task_id)
    values = payload.model_dump(exclude_unset=True)
    if "title" in values:
        task.title = " ".join(values["title"].split())
    if "description" in values:
        task.description = values["description"]
    if "priority" in values:
        task.priority = values["priority"]
        task.priority_source = PrioritySource.MANUAL
    if "due_at" in values:
        task.due_at = BusinessCalendar(config).normalize_due(values["due_at"])
        task.due_reminder_sent_at = None
        task.overdue_notification_date = None
    if "status" in values:
        task.status = values["status"]
        if task.status == TaskStatus.COMPLETED:
            task.completed_at = BusinessCalendar(config).now()
        elif task.completed_at:
            task.completed_at = None
    await rebuild_plan(session, BusinessCalendar(config).now())
    await session.commit()
    return await _get_task(session, task.id)


@router.post("/{task_id}/complete", response_model=TaskRead)
async def complete_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> Task:
    task = await _get_task(session, task_id)
    now = BusinessCalendar(config).now()
    task.status = TaskStatus.COMPLETED
    task.completed_at = now
    await rebuild_plan(session, now)
    await session.commit()
    return await _get_task(session, task.id)


@router.post("/{task_id}/confirm", response_model=TaskRead)
async def confirm_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> Task:
    task = await _get_task(session, task_id)
    if task.status != TaskStatus.NEEDS_CONFIRMATION:
        raise HTTPException(status_code=409, detail="Task does not require confirmation")
    task.status = TaskStatus.NEW
    await rebuild_plan(session, BusinessCalendar(config).now())
    await session.commit()
    return await _get_task(session, task.id)


@router.post("/{task_id}/reject", response_model=TaskRead)
async def reject_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> Task:
    task = await _get_task(session, task_id)
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Completed task cannot be rejected")
    task.status = TaskStatus.CANCELLED
    task.completed_at = None
    for reminder in task.reminders:
        reminder.enabled = False
    await rebuild_plan(session, BusinessCalendar(config).now())
    await session.commit()
    return await _get_task(session, task.id)


@router.post("/{task_id}/reminders", response_model=ReminderRead, status_code=201)
async def create_reminder(
    task_id: uuid.UUID,
    payload: ReminderCreate,
    session: AsyncSession = Depends(get_session),
) -> Reminder:
    await _get_task(session, task_id)
    reminder = Reminder(task_id=task_id, remind_at=payload.remind_at)
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


@router.delete("/{task_id}/reminders/{reminder_id}", status_code=204)
async def delete_reminder(
    task_id: uuid.UUID,
    reminder_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None or reminder.task_id != task_id:
        raise HTTPException(status_code=404, detail="Reminder not found")
    await session.delete(reminder)
    await session.commit()
