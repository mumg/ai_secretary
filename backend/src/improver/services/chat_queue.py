from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, or_, select

from improver.config import AppConfig
from improver.enums import ChatRequestStatus
from improver.models import ChatRequest
from improver.schemas import ChatHistoryMessage
from improver.services.archive_chat import ArchiveChatService
from improver.services.chat_context import ChatContextError
from improver.services.notifications import NotificationService

log = structlog.get_logger()


def chat_retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * (2 ** max(attempts - 1, 0)), 3_600))


async def claim_chat_request(now: datetime) -> uuid.UUID | None:
    from improver.db import SessionFactory

    stale_before = now - timedelta(minutes=15)
    async with SessionFactory() as session:
        result = await session.execute(
            select(ChatRequest)
            .where(
                or_(
                    and_(
                        ChatRequest.status == ChatRequestStatus.PENDING,
                        or_(
                            ChatRequest.next_attempt_at.is_(None),
                            ChatRequest.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        ChatRequest.status == ChatRequestStatus.PROCESSING,
                        ChatRequest.started_at < stale_before,
                    ),
                )
            )
            .order_by(ChatRequest.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        request = result.scalar_one_or_none()
        if request is None:
            return None
        request.status = ChatRequestStatus.PROCESSING
        request.started_at = now
        request.next_attempt_at = None
        request.error = None
        request.attempts += 1
        request_id = request.id
        await session.commit()
        return request_id


async def process_next_chat_request(
    config: AppConfig,
    now: datetime | None = None,
) -> bool:
    from improver.db import SessionFactory

    now = now or datetime.now(UTC)
    request_id = await claim_chat_request(now)
    if request_id is None:
        return False

    try:
        async with SessionFactory() as session:
            request = await session.get(ChatRequest, request_id)
            if request is None:
                return True
            answer, references = await ArchiveChatService(session, config).answer(
                query=request.query,
                history=[ChatHistoryMessage.model_validate(item) for item in request.history],
                tag_ids=[uuid.UUID(value) for value in request.tag_ids],
            )
            request.answer = answer
            request.references = [reference.model_dump(mode="json") for reference in references]
            request.status = ChatRequestStatus.COMPLETED
            request.error = None
            request.next_attempt_at = None
            request.completed_at = datetime.now(UTC)
            await session.commit()
            await NotificationService(config).send(
                session,
                "CHAT_RESPONSE_READY",
                str(request.id),
            )
        log.info("chat_request_completed", request_id=str(request_id))
    except Exception as exc:
        async with SessionFactory() as session:
            request = await session.get(ChatRequest, request_id)
            if request is not None:
                retryable = not isinstance(exc, ChatContextError)
                request.status = (
                    ChatRequestStatus.PENDING if retryable else ChatRequestStatus.FAILED
                )
                request.error = (str(exc) or type(exc).__name__)[:2_000]
                request.next_attempt_at = (
                    datetime.now(UTC) + chat_retry_delay(request.attempts) if retryable else None
                )
                await session.commit()
                log.exception(
                    "chat_request_retry_scheduled" if retryable else "chat_request_failed",
                    request_id=str(request_id),
                    attempts=request.attempts,
                    next_attempt_at=(
                        request.next_attempt_at.isoformat() if request.next_attempt_at else None
                    ),
                )
        return True
    return True
