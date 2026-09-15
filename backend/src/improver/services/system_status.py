from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig
from improver.enums import AnalysisState, ChatRequestStatus, ComponentHealthStatus, TaskStatus
from improver.models import (
    ChatRequest,
    CommunicationEvent,
    CommunicationSource,
    ComponentStatus,
    MeetingContext,
    Task,
)
from improver.schemas import ComponentStatusRead, SystemStatusRead
from improver.services.ollama import OLLAMA_ADVISORY_LOCK_ID

STATUS_SEVERITY = {
    ComponentHealthStatus.OK: 0,
    ComponentHealthStatus.DISABLED: 0,
    ComponentHealthStatus.BUSY: 1,
    ComponentHealthStatus.UNKNOWN: 2,
    ComponentHealthStatus.DEGRADED: 3,
    ComponentHealthStatus.STALE: 3,
    ComponentHealthStatus.ERROR: 4,
}


def _safe_message(value: str | None, limit: int = 500) -> str | None:
    if not value:
        return None
    return " ".join(value.split())[:limit] or None


async def upsert_component_status(
    session: AsyncSession,
    *,
    component_id: str,
    label: str,
    component_type: str,
    status: str,
    message: str | None,
    metrics: dict[str, float],
    observed_at: datetime | None = None,
    ttl_seconds: int | None = 300,
) -> ComponentStatus:
    observed = observed_at or datetime.now(UTC)
    expires_at = observed + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
    row = await session.get(ComponentStatus, component_id)
    if row is not None and row.observed_at > observed:
        return row
    if row is None:
        row = ComponentStatus(id=component_id)
        session.add(row)
    row.label = label
    row.component_type = component_type
    row.status = status
    row.message = _safe_message(message, 2_000)
    row.metrics = metrics
    row.observed_at = observed
    row.expires_at = expires_at
    await session.flush()
    return row


def _read_persisted(row: ComponentStatus, now: datetime) -> ComponentStatusRead:
    status = ComponentHealthStatus(row.status)
    message = row.message
    if row.expires_at is not None and row.expires_at <= now:
        status = ComponentHealthStatus.STALE
        message = message or "Heartbeat не обновлён вовремя"
    return ComponentStatusRead(
        id=row.id,
        label=row.label,
        component_type=row.component_type,
        status=status,
        message=message,
        metrics=row.metrics,
        observed_at=row.observed_at,
        expires_at=row.expires_at,
    )


async def _source_components(
    session: AsyncSession,
    config: AppConfig,
    now: datetime,
    persisted_by_id: dict[str, ComponentStatus],
) -> list[ComponentStatusRead]:
    rows = list(
        (
            await session.execute(select(CommunicationSource).order_by(CommunicationSource.label))
        ).scalars()
    )
    configured = {source.id: source for source in config.communication_sources.items}
    result: list[ComponentStatusRead] = []
    for row in rows:
        if row.source_type == "external_tasks":
            continue
        runtime = persisted_by_id.get(f"source-{row.id}")
        if row.enabled and runtime is not None:
            result.append(_read_persisted(runtime, now))
            continue
        source = configured.get(row.id)
        poll_seconds = (
            source.poll_interval_seconds
            if source is not None and source.poll_interval_seconds is not None
            else config.worker.poll_interval_seconds
        )
        stale_after = max(300, poll_seconds * 3)
        metrics: dict[str, float] = {"poll_interval_seconds": float(poll_seconds)}
        if row.last_sync_at is not None:
            metrics["seconds_since_sync"] = max(0.0, (now - row.last_sync_at).total_seconds())
        if not row.enabled:
            status = ComponentHealthStatus.DISABLED
            message = "Источник отключён"
        elif row.last_error:
            status = ComponentHealthStatus.ERROR
            message = "Последняя загрузка завершилась ошибкой"
        elif row.last_sync_at is None:
            status = ComponentHealthStatus.UNKNOWN
            message = "Успешная загрузка ещё не зафиксирована"
        elif (now - row.last_sync_at).total_seconds() > stale_after:
            status = ComponentHealthStatus.DEGRADED
            message = "Данные не обновлялись дольше ожидаемого"
        else:
            status = ComponentHealthStatus.OK
            message = None
        result.append(
            ComponentStatusRead(
                id=f"source:{row.id}",
                label=row.label,
                component_type="event_loader",
                status=status,
                message=message,
                metrics=metrics,
                observed_at=row.last_sync_at or row.updated_at,
            )
        )
    return result


async def _ollama_component(config: AppConfig, now: datetime) -> ComponentStatusRead:
    started = monotonic()
    metrics: dict[str, float] = {}
    version: str | None = None
    label = "LLMOps / OpenAI API" if config.llm.provider == "openai" else "Ollama"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            headers = config.llm.request_headers()
            if config.llm.provider == "openai":
                response = await client.get(config.llm.api_url("models"), headers=headers)
                response.raise_for_status()
                names = {str(item.get("id", "")) for item in response.json().get("data", [])}
            else:
                version_response = await client.get(
                    config.llm.api_url("api/version"), headers=headers
                )
                version_response.raise_for_status()
                version = str(version_response.json().get("version") or "unknown")
                tags_response = await client.get(config.llm.api_url("api/tags"), headers=headers)
                tags_response.raise_for_status()
                models = tags_response.json().get("models", [])
                names = {str(item.get("name", "")) for item in models if isinstance(item, dict)}
        metrics["latency_ms"] = round((monotonic() - started) * 1000, 1)
        metrics["model_count"] = float(len(names))
        expected = config.llm.model
        available = expected in names or (
            config.llm.provider == "ollama"
            and ":" not in expected
            and f"{expected}:latest" in names
        )
        status = ComponentHealthStatus.OK if available else ComponentHealthStatus.DEGRADED
        message = (
            (f"Ollama {version}" if version else "API модели доступен")
            if available
            else f"Модель {expected} отсутствует в списке доступных моделей"
        )
    except Exception as exc:
        metrics["latency_ms"] = round((monotonic() - started) * 1000, 1)
        status = ComponentHealthStatus.ERROR
        message = f"{label} недоступен ({type(exc).__name__})"
    return ComponentStatusRead(
        id="ollama",
        label=label,
        component_type="llm",
        status=status,
        message=message,
        metrics=metrics,
        observed_at=now,
    )


async def _status_counts(session: AsyncSession, model: type, column) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).select_from(model).group_by(column))
    return {str(status): int(count) for status, count in rows}


async def _processing_component(session: AsyncSession, now: datetime) -> ComponentStatusRead:
    event_counts = await _status_counts(
        session, CommunicationEvent, CommunicationEvent.analysis_state
    )
    chat_counts = await _status_counts(session, ChatRequest, ChatRequest.status)
    context_counts = await _status_counts(session, MeetingContext, MeetingContext.status)
    active_tasks = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.CANCELLED]))
    )
    retry_waiting = await session.scalar(
        select(func.count())
        .select_from(CommunicationEvent)
        .where(
            CommunicationEvent.analysis_state == AnalysisState.PENDING,
            CommunicationEvent.next_analysis_at > now,
        )
    )
    stale_cutoff = now - timedelta(minutes=15)
    stuck_events = await session.scalar(
        select(func.count())
        .select_from(CommunicationEvent)
        .where(
            CommunicationEvent.analysis_state == AnalysisState.PROCESSING,
            CommunicationEvent.updated_at < stale_cutoff,
        )
    )
    stuck_chats = await session.scalar(
        select(func.count())
        .select_from(ChatRequest)
        .where(
            ChatRequest.status == ChatRequestStatus.PROCESSING,
            ChatRequest.started_at < stale_cutoff,
        )
    )
    stuck_contexts = await session.scalar(
        select(func.count())
        .select_from(MeetingContext)
        .where(
            MeetingContext.status == "PROCESSING",
            MeetingContext.started_at < stale_cutoff,
        )
    )
    metrics = {
        "events_pending": float(event_counts.get(AnalysisState.PENDING, 0)),
        "events_processing": float(event_counts.get(AnalysisState.PROCESSING, 0)),
        "events_failed": float(event_counts.get(AnalysisState.FAILED, 0)),
        "events_retry_waiting": float(retry_waiting or 0),
        "chat_pending": float(chat_counts.get(ChatRequestStatus.PENDING, 0)),
        "chat_processing": float(chat_counts.get(ChatRequestStatus.PROCESSING, 0)),
        "chat_failed": float(chat_counts.get(ChatRequestStatus.FAILED, 0)),
        "contexts_pending": float(context_counts.get("PENDING", 0)),
        "contexts_processing": float(context_counts.get("PROCESSING", 0)),
        "contexts_failed": float(context_counts.get("FAILED", 0)),
        "stuck": float((stuck_events or 0) + (stuck_chats or 0) + (stuck_contexts or 0)),
        "tasks_active": float(active_tasks or 0),
    }
    failed = metrics["events_failed"] + metrics["chat_failed"] + metrics["contexts_failed"]
    queued = (
        metrics["events_pending"]
        + metrics["events_processing"]
        + metrics["chat_pending"]
        + metrics["chat_processing"]
        + metrics["contexts_pending"]
        + metrics["contexts_processing"]
    )
    if metrics["stuck"]:
        status = ComponentHealthStatus.ERROR
        message = "Есть обработчики без прогресса более 15 минут"
    elif failed:
        status = ComponentHealthStatus.DEGRADED
        message = "Есть завершившиеся с ошибкой операции"
    elif queued:
        status, message = ComponentHealthStatus.BUSY, "Очереди обрабатываются"
    else:
        status, message = ComponentHealthStatus.OK, None
    return ComponentStatusRead(
        id="processing",
        label="Обработка",
        component_type="processing",
        status=status,
        message=message,
        metrics=metrics,
        observed_at=now,
    )


async def _semaphore_component(session: AsyncSession, now: datetime) -> ComponentStatusRead:
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE granted) AS holders, "
                "count(*) FILTER (WHERE NOT granted) AS waiters "
                "FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 "
                "AND objid = :lock_id"
            ),
            {"lock_id": OLLAMA_ADVISORY_LOCK_ID},
        )
    ).one()
    ready_chat = await session.scalar(
        select(func.count())
        .select_from(ChatRequest)
        .where(
            ChatRequest.status == ChatRequestStatus.PENDING,
            (ChatRequest.next_attempt_at.is_(None)) | (ChatRequest.next_attempt_at <= now),
        )
    )
    holders = int(row.holders or 0)
    waiters = int(row.waiters or 0)
    status = ComponentHealthStatus.BUSY if holders or waiters else ComponentHealthStatus.OK
    return ComponentStatusRead(
        id="ollama-semaphore",
        label="Семафор модели",
        component_type="semaphore",
        status=status,
        message="Модель занята" if status == ComponentHealthStatus.BUSY else None,
        metrics={
            "capacity": 1.0,
            "in_use": float(holders),
            "waiting": float(waiters),
            "interactive_ready": float(ready_chat or 0),
        },
        observed_at=now,
    )


async def build_system_status(session: AsyncSession, config: AppConfig) -> SystemStatusRead:
    now = datetime.now(UTC)
    persisted_rows = list(
        (await session.execute(select(ComponentStatus).order_by(ComponentStatus.label))).scalars()
    )
    persisted_by_id = {row.id: row for row in persisted_rows}
    persisted = [
        _read_persisted(row, now)
        for row in persisted_rows
        if not (row.component_type == "event_loader" and row.id.startswith("source-"))
    ]
    if not any(component.component_type == "worker" for component in persisted):
        persisted.append(
            ComponentStatusRead(
                id="worker-main",
                label="Worker",
                component_type="worker",
                status=ComponentHealthStatus.UNKNOWN,
                message="Heartbeat ещё не получен",
                observed_at=now,
            )
        )
    components = [
        *await _source_components(session, config, now, persisted_by_id),
        await _ollama_component(config, now),
        await _processing_component(session, now),
        await _semaphore_component(session, now),
        *persisted,
    ]
    overall = max(
        (component.status for component in components),
        key=lambda status: STATUS_SEVERITY[status],
        default=ComponentHealthStatus.UNKNOWN,
    )
    return SystemStatusRead(overall_status=overall, generated_at=now, components=components)
