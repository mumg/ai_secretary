from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.db import get_session
from improver.enums import AnalysisState
from improver.models import CommunicationEvent, CommunicationSource, ConversationThread
from improver.schemas import (
    ConversationEventRead,
    ConversationThreadDetail,
    ConversationThreadPage,
    ConversationThreadRead,
)
from improver.services.full_text_search import full_text_match, normalize_search_query
from improver.services.meeting_results import MEETING_TRANSCRIPT_EVENT_TYPE
from improver.services.meetings import MEETING_EVENT_TYPE
from improver.services.text import bounded_text

router = APIRouter(prefix="/threads", tags=["threads"])


def _thread_read(
    thread: ConversationThread,
    source_label: str | None,
) -> ConversationThreadRead:
    return ConversationThreadRead(
        id=thread.id,
        source_id=thread.source_id,
        source_label=source_label or thread.source_id,
        source_type=thread.source_type,
        title=thread.title,
        participants=thread.participants,
        summary=thread.summary,
        event_count=thread.event_count,
        first_event_at=thread.first_event_at,
        last_event_at=thread.last_event_at,
        summarized_at=thread.summarized_at,
    )


@router.get("", response_model=ConversationThreadPage)
async def list_threads(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    query: str | None = Query(None, alias="q", max_length=200),
    session: AsyncSession = Depends(get_session),
) -> ConversationThreadPage:
    conditions = [
        exists(
            select(CommunicationEvent.id).where(
                CommunicationEvent.source_id == ConversationThread.source_id,
                CommunicationEvent.thread_external_id
                == ConversationThread.thread_external_id,
                CommunicationEvent.event_type.not_in(
                    [MEETING_EVENT_TYPE, MEETING_TRANSCRIPT_EVENT_TYPE]
                ),
                CommunicationEvent.analysis_state.not_in(
                    [AnalysisState.SKIPPED, AnalysisState.IGNORED]
                ),
                CommunicationEvent.is_mailing.is_(False),
            )
        )
    ]
    normalized_query = normalize_search_query(query)
    if normalized_query:
        conditions.append(full_text_match("conversation_threads", normalized_query))
    result = await session.execute(
        select(ConversationThread, CommunicationSource.label)
        .outerjoin(CommunicationSource, CommunicationSource.id == ConversationThread.source_id)
        .where(*conditions)
        .order_by(ConversationThread.last_event_at.desc(), ConversationThread.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.all())
    has_more = len(rows) > limit
    items = [
        _thread_read(thread, source_label)
        for thread, source_label in rows[:limit]
    ]
    return ConversationThreadPage(
        items=items,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


@router.get("/{thread_id}", response_model=ConversationThreadDetail)
async def get_thread(
    thread_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ConversationThreadDetail:
    row = (
        await session.execute(
            select(ConversationThread, CommunicationSource.label)
            .outerjoin(
                CommunicationSource,
                CommunicationSource.id == ConversationThread.source_id,
            )
            .where(ConversationThread.id == thread_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    thread, source_label = row
    event_conditions = (
        CommunicationEvent.source_id == thread.source_id,
        CommunicationEvent.thread_external_id == thread.thread_external_id,
        CommunicationEvent.event_type.not_in(
            [MEETING_EVENT_TYPE, MEETING_TRANSCRIPT_EVENT_TYPE]
        ),
        CommunicationEvent.analysis_state.not_in(
            [AnalysisState.SKIPPED, AnalysisState.IGNORED]
        ),
        CommunicationEvent.is_mailing.is_(False),
    )
    event_result = await session.execute(
        select(CommunicationEvent)
        .where(*event_conditions)
        .order_by(CommunicationEvent.occurred_at.desc(), CommunicationEvent.id.desc())
        .limit(101)
    )
    events = list(event_result.scalars())
    base = _thread_read(thread, source_label)
    total_events = int(
        await session.scalar(select(func.count(CommunicationEvent.id)).where(*event_conditions))
        or 0
    )
    return ConversationThreadDetail(
        **{**base.model_dump(), "event_count": total_events},
        events=[
            ConversationEventRead(
                id=event.id,
                event_type=event.event_type,
                direction=event.direction,
                subject=event.subject,
                author=event.author,
                occurred_at=event.occurred_at,
                preview=bounded_text(event.semantic_summary or event.body, 1_000),
                source_url=event.source_url,
            )
            for event in events[:100]
        ],
        has_more_events=total_events > 100,
    )
