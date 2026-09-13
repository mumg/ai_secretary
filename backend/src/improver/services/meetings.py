from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.models import CommunicationEvent, Meeting
from improver.schemas import MeetingRead
from improver.services.meeting_results import link_results_to_calendar_meeting
from improver.services.mts_link import find_mts_link_urls, mts_link_reference_keys

MEETING_EVENT_TYPE = "meeting_invitation"


def meeting_read(meeting: Meeting, source_label: str | None = None) -> MeetingRead:
    return MeetingRead(
        id=meeting.id,
        source_id=meeting.source_id,
        source_label=source_label or meeting.source_id,
        source_event_id=meeting.source_event_id,
        title=meeting.title,
        starts_at=meeting.starts_at,
        ends_at=meeting.ends_at,
        all_day=meeting.all_day,
        location=meeting.location,
        organizer=meeting.organizer,
        attendees=meeting.attendees,
        status=meeting.status,
        method=meeting.method,
        mts_link_url=meeting.mts_link_url,
    )


def meeting_payload(event: CommunicationEvent) -> dict[str, Any] | None:
    payload = (event.raw_headers or {}).get("Calendar-Event")
    return payload if isinstance(payload, dict) else None


async def upsert_meeting(
    session: AsyncSession,
    event: CommunicationEvent,
) -> Meeting | None:
    payload = meeting_payload(event)
    if payload is None:
        return None
    external_uid = str(payload.get("uid") or "").strip()
    if not external_uid:
        return None
    starts_at = datetime.fromisoformat(str(payload["starts_at"]))
    ends_at = datetime.fromisoformat(str(payload["ends_at"]))
    location = str(payload["location"])[:1000] if payload.get("location") else None
    urls = find_mts_link_urls(location, event.body, event.source_url)
    mts_link_url = urls[0] if urls else None
    mts_link_keys = mts_link_reference_keys(location, event.body, event.source_url)
    meeting = await session.scalar(
        select(Meeting)
        .where(
            Meeting.source_id == event.source_id,
            Meeting.external_uid == external_uid,
        )
        .with_for_update()
    )
    if meeting is None:
        meeting = Meeting(
            source_id=event.source_id,
            external_uid=external_uid,
            source_event_id=event.id,
            title=str(payload.get("title") or event.subject or "Встреча")[:500],
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=bool(payload.get("all_day")),
            location=location,
            mts_link_url=mts_link_url,
            mts_link_keys=mts_link_keys,
            organizer=(
                payload.get("organizer")
                if isinstance(payload.get("organizer"), dict)
                else None
            ),
            attendees=[item for item in payload.get("attendees", []) if isinstance(item, dict)][
                :200
            ],
            status=str(payload.get("status") or "CONFIRMED")[:32],
            method=str(payload.get("method") or "REQUEST")[:32],
            last_event_at=event.occurred_at,
        )
        session.add(meeting)
        await session.flush()
        await link_results_to_calendar_meeting(session, meeting, event)
        return meeting
    if event.occurred_at < meeting.last_event_at:
        return meeting
    meeting.source_event_id = event.id
    meeting.title = str(payload.get("title") or event.subject or "Встреча")[:500]
    meeting.starts_at = starts_at
    meeting.ends_at = ends_at
    meeting.all_day = bool(payload.get("all_day"))
    meeting.location = location
    meeting.mts_link_url = mts_link_url
    meeting.mts_link_keys = mts_link_keys
    meeting.organizer = (
        payload.get("organizer") if isinstance(payload.get("organizer"), dict) else None
    )
    meeting.attendees = [
        item for item in payload.get("attendees", []) if isinstance(item, dict)
    ][:200]
    meeting.status = str(payload.get("status") or "CONFIRMED")[:32]
    meeting.method = str(payload.get("method") or "REQUEST")[:32]
    meeting.last_event_at = event.occurred_at
    await session.flush()
    await link_results_to_calendar_meeting(session, meeting, event)
    return meeting


async def meetings_between(
    session: AsyncSession,
    starts_before: datetime,
    ends_after: datetime,
    include_cancelled: bool = False,
) -> list[Meeting]:
    query = (
        select(Meeting)
        .where(Meeting.starts_at < starts_before, Meeting.ends_at > ends_after)
        .order_by(Meeting.starts_at, Meeting.id)
    )
    if not include_cancelled:
        query = query.where(Meeting.status != "CANCELLED")
    return list((await session.execute(query)).scalars())
