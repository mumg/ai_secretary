from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.models import CommunicationSource, Meeting
from improver.schemas import MeetingPage
from improver.services.calendar import BusinessCalendar
from improver.services.full_text_search import full_text_match, normalize_search_query
from improver.services.meetings import meeting_read

router = APIRouter(prefix="/meetings", tags=["meetings"])


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
