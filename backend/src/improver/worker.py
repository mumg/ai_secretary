from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta

import structlog
from sqlalchemy import or_, select

from improver.config import AppConfig, get_config
from improver.connectors import connector_for
from improver.db import SessionFactory, engine
from improver.enums import AnalysisState, TaskStatus
from improver.models import (
    CommunicationEvent,
    CommunicationSource,
    ComponentStatus,
    DailyPlan,
    Reminder,
    Task,
)
from improver.schema_version import wait_for_compatible_database
from improver.services.calendar import BusinessCalendar
from improver.services.chat_queue import process_next_chat_request
from improver.services.meeting_context import (
    notify_next_meeting_context,
    prepare_next_meeting_context,
)
from improver.services.notifications import NotificationService
from improver.services.pipeline import EventPipeline
from improver.services.plans import rebuild_plan
from improver.services.scheduling import next_worker_delay, source_sync_due
from improver.services.settings import load_runtime_config
from improver.services.system_status import upsert_component_status

log = structlog.get_logger()


def source_error_message(exc: Exception) -> str:
    error_name = type(exc).__name__
    normalized = error_name.casefold()
    response = getattr(exc, "response", None)
    code = getattr(exc, "code", None) or getattr(response, "status_code", None)
    if code in {401, 403} or "auth" in normalized or "unauthorized" in normalized:
        return "Ошибка авторизации источника"
    if isinstance(code, int):
        return f"Источник вернул HTTP {code}"
    if isinstance(exc, TimeoutError) or "timeout" in normalized:
        return "Источник не ответил вовремя"
    return f"Ошибка загрузки данных ({error_name})"


async def semantic_backfill_loop() -> None:
    while True:
        indexed = 0
        mailings_classified = 0
        tasks_checked = 0
        try:
            async with SessionFactory() as session:
                config = await load_runtime_config(session)
            now = BusinessCalendar(config).now()
            async with SessionFactory() as session:
                pending_primary = await session.scalar(
                    select(CommunicationEvent.id)
                    .where(
                        CommunicationEvent.analysis_state == AnalysisState.PENDING,
                        or_(
                            CommunicationEvent.next_analysis_at.is_(None),
                            CommunicationEvent.next_analysis_at <= now,
                        ),
                    )
                    .limit(1)
                )
                if pending_primary is not None:
                    await asyncio.sleep(2)
                    continue
                pipeline = EventPipeline(config)
                tasks_checked = await pipeline.process_task_extraction_backfill(session, now)
                if not tasks_checked:
                    mailings_classified = await pipeline.process_mailing_backfill(session, now)
                if not tasks_checked and not mailings_classified:
                    indexed = await pipeline.process_semantic_backfill(session, now)
            if tasks_checked:
                log.info("task_extraction_events_checked", count=tasks_checked)
            if mailings_classified:
                log.info("mailing_events_classified", count=mailings_classified)
            if indexed:
                log.info("semantic_events_indexed", count=indexed)
        except Exception as exc:
            log.exception("semantic_backfill_loop_failed", error=str(exc))
        await asyncio.sleep(2 if indexed or mailings_classified or tasks_checked else 60)


async def chat_request_loop() -> None:
    while True:
        processed = False
        try:
            async with SessionFactory() as session:
                config = await load_runtime_config(session)
            processed = await process_next_chat_request(config)
        except Exception as exc:
            log.exception("chat_request_loop_failed", error=str(exc))
        await asyncio.sleep(0 if processed else 2)


async def meeting_context_loop() -> None:
    while True:
        processed = False
        try:
            async with SessionFactory() as session:
                config = await load_runtime_config(session)
            await notify_next_meeting_context(config)
            processed = await prepare_next_meeting_context(config)
        except Exception as exc:
            log.warning("meeting_context_loop_failed", error_type=type(exc).__name__)
        await asyncio.sleep(1 if processed else 15)


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


async def sync_sources(config: AppConfig, now: datetime) -> None:
    for source in config.communication_sources.items:
        if not source.enabled or source.type == "external_tasks":
            continue
        async with SessionFactory() as session:
            status_id = f"source-{source.id}"
            source_row = await session.get(CommunicationSource, source.id)
            status_row = await session.get(ComponentStatus, status_id)
            poll_interval_seconds = (
                source.poll_interval_seconds or config.worker.poll_interval_seconds
            )
            last_attempt_at = status_row.observed_at if status_row is not None else None
            if last_attempt_at is None and source_row is not None:
                last_attempt_at = source_row.last_sync_at
            if not source_sync_due(last_attempt_at, now, poll_interval_seconds):
                continue
            await upsert_component_status(
                session,
                component_id=status_id,
                label=source_row.label if source_row else source.id,
                component_type="event_loader",
                status="BUSY",
                message="Загрузка данных",
                metrics={"poll_interval_seconds": float(poll_interval_seconds)},
                observed_at=BusinessCalendar(config).now(),
                ttl_seconds=max(900, poll_interval_seconds * 3),
            )
            await session.commit()
            try:
                count = await connector_for(source, config).sync(session)
                row = await session.get(CommunicationSource, source.id)
                if row:
                    row.last_sync_at = now
                    row.last_error = None
                await upsert_component_status(
                    session,
                    component_id=status_id,
                    label=row.label if row else source.id,
                    component_type="event_loader",
                    status="OK",
                    message=None,
                    metrics={
                        "events_loaded": float(count),
                        "poll_interval_seconds": float(poll_interval_seconds),
                    },
                    observed_at=BusinessCalendar(config).now(),
                    ttl_seconds=max(900, poll_interval_seconds * 3),
                )
                await session.commit()
                if count:
                    log.info("source_synced", source_id=source.id, events=count)
            except Exception as exc:
                await session.rollback()
                row = await session.get(CommunicationSource, source.id)
                if row:
                    row.last_error = str(exc)[:2000]
                await upsert_component_status(
                    session,
                    component_id=status_id,
                    label=row.label if row else source.id,
                    component_type="event_loader",
                    status="ERROR",
                    message=source_error_message(exc),
                    metrics={"poll_interval_seconds": float(poll_interval_seconds)},
                    observed_at=BusinessCalendar(config).now(),
                    ttl_seconds=max(900, poll_interval_seconds * 3),
                )
                await session.commit()
                log.exception("source_sync_failed", source_id=source.id, error=str(exc))


async def ensure_daily_plan(config: AppConfig, now: datetime) -> bool:
    plan_time = config.calendar.daily_plan_time
    if now.strftime("%H:%M") < plan_time:
        return False
    async with SessionFactory() as session:
        exists = await session.scalar(select(DailyPlan.id).where(DailyPlan.plan_date == now.date()))
        if exists:
            return False
        plan = await rebuild_plan(session, now)
        await session.commit()
        await NotificationService(config).send(session, "DAILY_PLAN_READY", str(plan.id))
        return True


async def refresh_daily_plan_order(now: datetime) -> None:
    async with SessionFactory() as session:
        exists = await session.scalar(select(DailyPlan.id).where(DailyPlan.plan_date == now.date()))
        if not exists:
            return
        await rebuild_plan(session, now)
        await session.commit()


async def send_due_manual_reminders(config: AppConfig, now: datetime) -> None:
    async with SessionFactory() as session:
        result = await session.execute(
            select(Reminder)
            .join(Task, Reminder.task_id == Task.id)
            .where(
                Reminder.enabled.is_(True),
                Reminder.sent_at.is_(None),
                Reminder.remind_at <= now,
                Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
        )
        reminders = list(result.scalars())
        notifier = NotificationService(config)
        for reminder in reminders:
            delivered = await notifier.send(session, "TASK_REMINDER", str(reminder.task_id))
            if delivered:
                reminder.sent_at = now
        await session.commit()


async def send_automatic_due_notifications(config: AppConfig, now: datetime) -> None:
    active_statuses = [TaskStatus.NEW, TaskStatus.IN_PROGRESS]
    due_soon_until = now + timedelta(minutes=config.notifications.due_soon_minutes)
    async with SessionFactory() as session:
        notifier = NotificationService(config)
        due_soon_result = await session.execute(
            select(Task).where(
                Task.status.in_(active_statuses),
                Task.due_at.is_not(None),
                Task.due_at > now,
                Task.due_at <= due_soon_until,
                Task.due_reminder_sent_at.is_(None),
            )
        )
        for task in due_soon_result.scalars():
            delivered = await notifier.send(session, "TASK_DUE_SOON", str(task.id))
            if delivered:
                task.due_reminder_sent_at = now

        if now.hour >= config.notifications.overdue_repeat_hour:
            overdue_result = await session.execute(
                select(Task).where(
                    Task.status.in_(active_statuses),
                    Task.due_at.is_not(None),
                    Task.due_at < now,
                    (Task.overdue_notification_date.is_(None))
                    | (Task.overdue_notification_date != now.date()),
                )
            )
            for task in overdue_result.scalars():
                delivered = await notifier.send(session, "TASK_OVERDUE", str(task.id))
                if delivered:
                    task.overdue_notification_date = now.date()
        await session.commit()


async def report_worker_heartbeat(config: AppConfig, now: datetime, processed: int) -> None:
    async with SessionFactory() as session:
        await upsert_component_status(
            session,
            component_id="worker-main",
            label="Worker",
            component_type="worker",
            status="OK",
            message=None,
            metrics={
                "events_last_cycle": float(processed),
                "poll_interval_seconds": float(config.worker.poll_interval_seconds),
            },
            observed_at=now,
            ttl_seconds=max(
                config.worker.poll_interval_seconds * 3,
                config.llm.request_timeout_seconds + 120,
            ),
        )
        await session.commit()


async def run() -> None:
    configure_logging()
    await wait_for_compatible_database()
    loop = asyncio.get_running_loop()
    last_ranking_refresh = 0.0
    log.info("worker_started")
    backfill_task = asyncio.create_task(semantic_backfill_loop())
    chat_task = asyncio.create_task(chat_request_loop())
    meeting_context_task = asyncio.create_task(meeting_context_loop())
    try:
        while True:
            async with SessionFactory() as session:
                config = await load_runtime_config(session)
            calendar = BusinessCalendar(config)
            pipeline = EventPipeline(config)
            now = calendar.now()
            await report_worker_heartbeat(config, now, 0)
            async with SessionFactory() as session:
                processed = await pipeline.process_batch(session, now)
            if not processed:
                await sync_sources(config, now)
                async with SessionFactory() as session:
                    processed = await pipeline.process_batch(session, now)
            if processed:
                log.info("events_processed", count=processed)
            plan_created = await ensure_daily_plan(config, now)
            monotonic_now = loop.time()
            if (
                not plan_created
                and monotonic_now - last_ranking_refresh >= config.worker.ranking_interval_seconds
            ):
                await refresh_daily_plan_order(now)
                last_ranking_refresh = monotonic_now
            await send_due_manual_reminders(config, now)
            await send_automatic_due_notifications(config, now)
            await report_worker_heartbeat(config, calendar.now(), processed)
            await asyncio.sleep(next_worker_delay(processed, config.worker.poll_interval_seconds))
    finally:
        backfill_task.cancel()
        chat_task.cancel()
        meeting_context_task.cancel()
        with suppress(asyncio.CancelledError):
            await backfill_task
        with suppress(asyncio.CancelledError):
            await chat_task
        with suppress(asyncio.CancelledError):
            await meeting_context_task
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
