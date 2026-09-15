from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.models import CommunicationSource, Meeting
from improver.schemas import MeetingContextRead, MeetingPage
from improver.services.calendar import BusinessCalendar
from improver.services.full_text_search import full_text_match, normalize_search_query
from improver.services.meeting_context import ensure_context
from improver.services.meetings import meeting_read

router = APIRouter(prefix="/meetings", tags=["meetings"])


async def context_detail(meeting_id: uuid.UUID, session: AsyncSession, *, refresh=False):
    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id).with_for_update()
    )
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    label = await session.scalar(
        select(CommunicationSource.label).where(CommunicationSource.id == meeting.source_id)
    )
    ended = meeting.ends_at <= datetime.now(UTC)
    row = await ensure_context(
        session, meeting, force=refresh and not ended and meeting.status != "CANCELLED"
    )
    response = MeetingContextRead(
        meeting=meeting_read(meeting, label),
        status="CANCELLED" if meeting.status == "CANCELLED" else "ENDED" if ended else row.status,
        summary=row.summary,
        references=row.references,
        generated_at=row.generated_at,
        error=row.error,
        stale=row.summary is not None and row.input_fingerprint is None,
    )
    await session.commit()
    return response


@router.get("/{meeting_id}/context", response_model=MeetingContextRead)
async def get_meeting_context(meeting_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await context_detail(meeting_id, session)


@router.post("/{meeting_id}/context/refresh", response_model=MeetingContextRead)
async def refresh_meeting_context(
    meeting_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    return await context_detail(meeting_id, session, refresh=True)


@router.get("", response_model=MeetingPage)
async def list_meetings(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    query: str | None = Query(None, alias="q", max_length=200),
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> MeetingPage:
    now = BusinessCalendar(config).now()
    conditions = [Meeting.ends_at > now, Meeting.status != "CANCELLED"]
    normalized_query = normalize_search_query(query)
    if normalized_query:
        conditions.append(full_text_match("meetings", normalized_query))
    result = await session.execute(
        select(Meeting, CommunicationSource.label)
        .outerjoin(CommunicationSource, CommunicationSource.id == Meeting.source_id)
        .where(*conditions)
        .order_by(Meeting.starts_at, Meeting.id)
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list(result.all())
    return MeetingPage(
        items=[meeting_read(meeting, source_label) for meeting, source_label in rows[:limit]],
        offset=offset,
        limit=limit,
        has_more=len(rows) > limit,
    )
