from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.models import CommunicationEvent, CommunicationSource, Meeting, MeetingResult
from improver.schemas import (
    MeetingResultDetail,
    MeetingResultPage,
    MeetingResultRead,
    ParticipantMeetingSummary,
)
from improver.services.calendar import BusinessCalendar
from improver.services.full_text_search import full_text_match, normalize_search_query
from improver.services.meeting_results import (
    EMAIL_FOLLOWUP_ORIGIN,
    merge_unique,
    result_brief_summary,
)

router = APIRouter(prefix="/meeting-results", tags=["meeting-results"])


async def _children_by_parent(
    session: AsyncSession,
    parent_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[tuple[MeetingResult, CommunicationEvent, str]]]:
    grouped: dict[uuid.UUID, list[tuple[MeetingResult, CommunicationEvent, str]]] = {
        item_id: [] for item_id in parent_ids
    }
    if not parent_ids:
        return grouped
    rows = (
        await session.execute(
            select(MeetingResult, CommunicationEvent, CommunicationSource.label)
            .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
            .outerjoin(CommunicationSource, CommunicationSource.id == MeetingResult.source_id)
            .where(MeetingResult.parent_result_id.in_(parent_ids))
            .order_by(CommunicationEvent.occurred_at)
        )
    ).all()
    for child, event, source_label in rows:
        if child.parent_result_id is not None:
            grouped[child.parent_result_id].append(
                (child, event, source_label or child.source_id)
            )
    return grouped


def _read_result(
    meeting_result: MeetingResult,
    event: CommunicationEvent,
    source_label: str | None,
    children: list[tuple[MeetingResult, CommunicationEvent, str]],
) -> MeetingResultRead:
    child_results = [item[0] for item in children]
    return MeetingResultRead(
        id=meeting_result.id,
        source_id=meeting_result.source_id,
        source_label=source_label or meeting_result.source_id,
        source_event_id=meeting_result.source_event_id,
        calendar_meeting_id=meeting_result.calendar_meeting_id,
        title=meeting_result.title,
        starts_at=meeting_result.starts_at,
        ends_at=meeting_result.ends_at,
        owner_name=meeting_result.owner_name,
        meeting_url=meeting_result.meeting_url,
        transcript_status=meeting_result.transcript_status,
        summary=meeting_result.summary,
        decisions=merge_unique(
            meeting_result.decisions, *(item.decisions for item in child_results)
        ),
        agreements=merge_unique(
            meeting_result.agreements, *(item.agreements for item in child_results)
        ),
        analysis_state=event.analysis_state,
        analyzed_at=meeting_result.analyzed_at,
        origin_type=meeting_result.origin_type,
        supplement_count=len(child_results),
        brief_summary=result_brief_summary(meeting_result, child_results),
    )


@router.get("", response_model=MeetingResultPage)
async def list_meeting_results(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    query: str | None = Query(None, alias="q", max_length=200),
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> MeetingResultPage:
    now = BusinessCalendar(config).now()
    conditions = [
        MeetingResult.parent_result_id.is_(None),
        MeetingResult.starts_at <= now,
    ]
    normalized_query = normalize_search_query(query)
    if normalized_query:
        child = aliased(MeetingResult, name="meeting_result_children")
        conditions.append(
            or_(
                full_text_match("meeting_results", normalized_query),
                exists(
                    select(child.id).where(
                        child.parent_result_id == MeetingResult.id,
                        full_text_match("meeting_result_children", normalized_query),
                    )
                ),
            )
        )
    rows = list(
        (
            await session.execute(
                select(MeetingResult, CommunicationEvent, CommunicationSource.label)
                .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
                .outerjoin(CommunicationSource, CommunicationSource.id == MeetingResult.source_id)
                .where(*conditions)
                .order_by(MeetingResult.starts_at.desc(), MeetingResult.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    visible_rows = rows[:limit]
    children = await _children_by_parent(
        session, [meeting_result.id for meeting_result, _, _ in visible_rows]
    )
    items = [
        _read_result(
            meeting_result,
            event,
            source_label,
            children.get(meeting_result.id, []),
        )
        for meeting_result, event, source_label in visible_rows
    ]
    return MeetingResultPage(
        items=items,
        offset=offset,
        limit=limit,
        has_more=len(rows) > limit,
    )


@router.get("/{result_id}", response_model=MeetingResultDetail)
async def get_meeting_result(
    result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> MeetingResultDetail:
    now = BusinessCalendar(config).now()
    row = (
        await session.execute(
            select(MeetingResult, CommunicationEvent, CommunicationSource.label)
            .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
            .outerjoin(CommunicationSource, CommunicationSource.id == MeetingResult.source_id)
            .where(
                MeetingResult.id == result_id,
                MeetingResult.parent_result_id.is_(None),
                MeetingResult.starts_at <= now,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting result not found")
    meeting_result, event, source_label = row
    children = (await _children_by_parent(session, [meeting_result.id]))[meeting_result.id]
    base = _read_result(meeting_result, event, source_label, children)

    participants: list[dict[str, object]] = []
    if meeting_result.calendar_meeting_id:
        calendar = await session.get(Meeting, meeting_result.calendar_meeting_id)
        if calendar:
            if calendar.organizer:
                participants.append(calendar.organizer)
            participants.extend(calendar.attendees or [])
    participants.extend(event.participants or [])
    for _, child_event, _ in children:
        participants.extend(child_event.participants or [])
    unique_participants: list[dict[str, object]] = []
    seen: set[str] = set()
    for participant in participants:
        key = str(
            participant.get("address")
            or participant.get("external_id")
            or participant.get("name")
            or ""
        ).casefold()
        if key and key not in seen:
            seen.add(key)
            unique_participants.append(participant)

    email_rows = list(children)
    if meeting_result.origin_type == EMAIL_FOLLOWUP_ORIGIN:
        email_rows.insert(0, (meeting_result, event, source_label or meeting_result.source_id))
    participant_summaries = [
        ParticipantMeetingSummary(
            source_event_id=item.source_event_id,
            source_label=child_source_label,
            author=child_event.author,
            occurred_at=child_event.occurred_at,
            summary=item.summary or "Резюме не сформировано",
            decisions=item.decisions,
            agreements=item.agreements,
        )
        for item, child_event, child_source_label in email_rows
        if item.origin_type == EMAIL_FOLLOWUP_ORIGIN
    ]
    return MeetingResultDetail(
        **base.model_dump(),
        participants=unique_participants,
        participant_summaries=participant_summaries,
    )
