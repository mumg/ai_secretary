from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.enums import AnalysisState
from improver.models import CommunicationEvent, ConversationThread
from improver.services.ollama import SemanticAnalysis
from improver.services.text import bounded_text

REPLY_PREFIX = re.compile(r"^(?:(?:re|fw|fwd|ответ|пересылка)\s*:\s*)+", re.IGNORECASE)


def thread_key(event: CommunicationEvent) -> str:
    return (event.thread_external_id or event.external_id).strip()


def thread_title(event: CommunicationEvent) -> str:
    subject = REPLY_PREFIX.sub("", (event.subject or "").strip()).strip()
    if subject:
        return bounded_text(subject, 500)
    if event.author:
        return bounded_text(f"Переписка с {event.author}", 500)
    return "Переписка без темы"


def merge_participants(
    current: list[dict[str, object]], incoming: list[dict[str, object]]
) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for participant in [*current, *incoming]:
        key = str(
            participant.get("address")
            or participant.get("email")
            or participant.get("name")
            or participant
        ).casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(participant)
    return merged[:100]


async def update_conversation_thread(
    session: AsyncSession,
    event: CommunicationEvent,
    analysis: SemanticAnalysis | None,
    now: datetime,
) -> ConversationThread:
    external_id = thread_key(event)
    event.thread_external_id = external_id
    await session.flush()

    thread = await session.scalar(
        select(ConversationThread)
        .where(
            ConversationThread.source_id == event.source_id,
            ConversationThread.thread_external_id == external_id,
        )
        .with_for_update()
    )
    aggregate = (
        await session.execute(
            select(
                func.min(CommunicationEvent.occurred_at),
                func.max(CommunicationEvent.occurred_at),
                func.count(CommunicationEvent.id),
            ).where(
                CommunicationEvent.source_id == event.source_id,
                CommunicationEvent.thread_external_id == external_id,
                CommunicationEvent.analysis_state.not_in(
                    [AnalysisState.SKIPPED, AnalysisState.IGNORED]
                ),
                CommunicationEvent.is_mailing.is_(False),
                CommunicationEvent.event_type.not_in(
                    ["meeting_invitation", "meeting_transcript"]
                ),
            )
        )
    ).one()
    first_event_at = aggregate[0] or event.occurred_at
    last_event_at = aggregate[1] or event.occurred_at
    event_count = int(aggregate[2])

    if thread is None:
        thread = ConversationThread(
            source_id=event.source_id,
            source_type=event.source_type,
            thread_external_id=external_id,
            title=thread_title(event),
            participants=event.participants,
            summary=None,
            event_count=event_count,
            first_event_at=first_event_at,
            last_event_at=last_event_at,
            latest_event_id=event.id,
        )
        session.add(thread)

    is_latest = event.occurred_at >= thread.last_event_at
    thread.event_count = event_count
    thread.first_event_at = first_event_at
    thread.last_event_at = last_event_at
    thread.participants = merge_participants(thread.participants, event.participants)
    if is_latest:
        thread.source_type = event.source_type
        thread.title = thread_title(event)
        thread.latest_event_id = event.id
        if analysis is not None:
            summary = analysis.thread_summary or analysis.summary
            if summary:
                thread.summary = bounded_text(summary, 8_000)
                thread.summary_model = event.analysis_model or "pending"
                thread.summarized_at = now
    return thread


async def rebuild_conversation_thread(
    session: AsyncSession,
    source_id: str,
    external_id: str,
    now: datetime,
) -> ConversationThread | None:
    """Rebuild one thread after a message is classified as a mailing."""
    thread = await session.scalar(
        select(ConversationThread)
        .where(
            ConversationThread.source_id == source_id,
            ConversationThread.thread_external_id == external_id,
        )
        .with_for_update()
    )
    events = list(
        (
            await session.execute(
                select(CommunicationEvent)
                .where(
                    CommunicationEvent.source_id == source_id,
                    CommunicationEvent.thread_external_id == external_id,
                    CommunicationEvent.analysis_state.not_in(
                        [AnalysisState.SKIPPED, AnalysisState.IGNORED]
                    ),
                    CommunicationEvent.is_mailing.is_(False),
                    CommunicationEvent.event_type.not_in(
                        ["meeting_invitation", "meeting_transcript"]
                    ),
                )
                .order_by(CommunicationEvent.occurred_at, CommunicationEvent.id)
            )
        ).scalars()
    )
    if not events:
        if thread is not None:
            await session.delete(thread)
        return None

    latest = events[-1]
    summary_event = next(
        (
            item
            for item in reversed(events)
            if item.analysis_state == AnalysisState.COMPLETED and item.semantic_summary
        ),
        None,
    )
    participants: list[dict[str, object]] = []
    for item in events:
        participants = merge_participants(participants, item.participants)

    if thread is None:
        thread = ConversationThread(
            source_id=source_id,
            source_type=latest.source_type,
            thread_external_id=external_id,
            title=thread_title(latest),
            participants=participants,
            event_count=len(events),
            first_event_at=events[0].occurred_at,
            last_event_at=latest.occurred_at,
            latest_event_id=latest.id,
        )
        session.add(thread)
    else:
        thread.source_type = latest.source_type
        thread.title = thread_title(latest)
        thread.participants = participants
        thread.event_count = len(events)
        thread.first_event_at = events[0].occurred_at
        thread.last_event_at = latest.occurred_at
        thread.latest_event_id = latest.id

    if summary_event is not None:
        thread.summary = bounded_text(summary_event.semantic_summary, 8_000)
        thread.summary_model = summary_event.analysis_model
        thread.summarized_at = summary_event.analyzed_at or now
    else:
        thread.summary = None
        thread.summary_model = None
        thread.summarized_at = None
    return thread
