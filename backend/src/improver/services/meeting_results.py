from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.models import CommunicationEvent, Meeting, MeetingResult
from improver.services.mts_link import (
    find_mts_link_urls,
    mts_link_reference_keys,
    references_overlap,
)
from improver.services.ollama import MeetingResultSignal, SemanticAnalysis

MEETING_TRANSCRIPT_EVENT_TYPE = "meeting_transcript"
MTS_TRANSCRIPT_ORIGIN = "mts_transcript"
EMAIL_FOLLOWUP_ORIGIN = "email_followup"
MEETING_RESULT_CONFIDENCE = 0.78

_MAIL_PREFIX_RE = re.compile(r"^(?:(?:re|fw|fwd|ответ|пересылка)\s*:\s*)+", re.I)
_RESULT_PREFIX_RE = re.compile(
    r"^(?:(?:итоги|результаты|протокол|резюме|follow[ -]?up|minutes)"
    r"(?:\s+(?:встречи|совещания|собрания|meeting))?\s*[:—-]\s*)+",
    re.I,
)


def normalize_meeting_title(value: str | None) -> str:
    title = " ".join((value or "").split()).strip()
    previous = None
    while title and title != previous:
        previous = title
        title = _MAIL_PREFIX_RE.sub("", title).strip()
        title = _RESULT_PREFIX_RE.sub("", title).strip()
    return re.sub(r"[^\wа-яё]+", " ", title.casefold(), flags=re.I).strip()


def merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group or []:
            normalized = " ".join(value.split()).strip()
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                merged.append(normalized)
    return merged


def closest_completed_meeting(
    meetings: list[Meeting], occurred_at: datetime
) -> Meeting | None:
    """Return the most recent plausible meeting for a follow-up message."""
    earliest = occurred_at - timedelta(days=45)
    candidates = [
        meeting
        for meeting in meetings
        if earliest <= meeting.starts_at and meeting.ends_at <= occurred_at
    ]
    return max(candidates, key=lambda item: item.starts_at) if candidates else None


def result_brief_summary(result: MeetingResult, supplements: list[MeetingResult]) -> str:
    agreements = merge_unique(result.agreements, *(item.agreements for item in supplements))
    decisions = merge_unique(result.decisions, *(item.decisions for item in supplements))
    values = agreements or decisions
    fallback_summary = next(
        (item.summary for item in [result, *supplements] if item.summary), ""
    )
    text = " • ".join(values[:3]) if values else fallback_summary
    text = " ".join(text.split()).strip()
    return text if len(text) <= 360 else text[:359].rstrip() + "…"


async def _calendar_candidates(
    session: AsyncSession,
) -> list[tuple[Meeting, CommunicationEvent]]:
    result = await session.execute(
        select(Meeting, CommunicationEvent).join(
            CommunicationEvent, CommunicationEvent.id == Meeting.source_event_id
        )
    )
    return list(result.all())


async def link_result_to_calendar(
    session: AsyncSession,
    meeting_result: MeetingResult,
) -> Meeting | None:
    matches: list[tuple[Meeting, CommunicationEvent, list[str]]] = []
    for meeting, event in await _calendar_candidates(session):
        if meeting.starts_at > meeting_result.ends_at:
            continue
        keys = meeting.mts_link_keys or mts_link_reference_keys(
            meeting.location, event.body, event.source_url
        )
        if references_overlap(meeting_result.mts_link_keys, keys):
            matches.append((meeting, event, keys))
    if not matches:
        return None
    meeting, event, keys = max(matches, key=lambda item: item[0].starts_at)
    meeting.mts_link_keys = keys
    if not meeting.mts_link_url:
        urls = find_mts_link_urls(meeting.location, event.body, event.source_url)
        meeting.mts_link_url = urls[0] if urls else None
    meeting_result.calendar_meeting_id = meeting.id
    return meeting


async def _consolidate_calendar_results(
    session: AsyncSession,
    calendar_meeting_id: object,
) -> None:
    results = list(
        (
            await session.execute(
                select(MeetingResult).where(
                    MeetingResult.calendar_meeting_id == calendar_meeting_id
                )
            )
        ).scalars()
    )
    mts_root = next(
        (
            item
            for item in results
            if item.origin_type == MTS_TRANSCRIPT_ORIGIN and item.parent_result_id is None
        ),
        None,
    )
    if mts_root is None:
        return
    for item in results:
        if item.id != mts_root.id and item.origin_type == EMAIL_FOLLOWUP_ORIGIN:
            item.parent_result_id = mts_root.id


async def link_results_to_calendar_meeting(
    session: AsyncSession,
    meeting: Meeting,
    event: CommunicationEvent,
) -> int:
    keys = meeting.mts_link_keys or mts_link_reference_keys(
        meeting.location, event.body, event.source_url
    )
    linked = 0
    if keys:
        result = await session.execute(select(MeetingResult))
        for meeting_result in result.scalars():
            if (
                meeting.starts_at <= meeting_result.ends_at
                and references_overlap(keys, meeting_result.mts_link_keys)
            ):
                previous_id = meeting_result.calendar_meeting_id
                previous = (
                    await session.get(Meeting, previous_id)
                    if previous_id is not None
                    else None
                )
                previous_is_better = (
                    previous is not None
                    and previous.starts_at <= meeting_result.ends_at
                    and previous.starts_at >= meeting.starts_at
                )
                if previous_is_better:
                    continue
                meeting_result.calendar_meeting_id = meeting.id
                if previous_id is not None:
                    await _consolidate_calendar_results(session, previous_id)
                await _consolidate_calendar_results(session, meeting.id)
                linked += 1
    await _consolidate_calendar_results(session, meeting.id)
    return linked


async def _find_calendar_for_email(
    session: AsyncSession,
    event: CommunicationEvent,
    signal: MeetingResultSignal,
    keys: list[str],
) -> Meeting | None:
    candidates = await _calendar_candidates(session)
    completed_candidates = [
        (meeting, calendar_event)
        for meeting, calendar_event in candidates
        if meeting.ends_at <= event.occurred_at
    ]
    if keys:
        reference_matches: list[Meeting] = []
        for meeting, calendar_event in completed_candidates:
            calendar_keys = meeting.mts_link_keys or mts_link_reference_keys(
                meeting.location, calendar_event.body, calendar_event.source_url
            )
            if references_overlap(keys, calendar_keys):
                reference_matches.append(meeting)
        if reference_matches:
            return max(reference_matches, key=lambda item: item.starts_at)
    if event.thread_external_id:
        thread_matches = [
            meeting
            for meeting, calendar_event in completed_candidates
            if (
                calendar_event.source_id == event.source_id
                and calendar_event.thread_external_id == event.thread_external_id
            )
        ]
        closest = closest_completed_meeting(thread_matches, event.occurred_at)
        if closest is not None:
            return closest
    title = normalize_meeting_title(signal.meeting_title or event.subject)
    if not title:
        return None
    earliest = event.occurred_at - timedelta(days=45)
    matches = [
        meeting
        for meeting, _ in completed_candidates
        if earliest <= meeting.starts_at
        and normalize_meeting_title(meeting.title) == title
    ]
    return max(matches, key=lambda item: item.starts_at) if matches else None


async def _find_existing_root(
    session: AsyncSession,
    calendar: Meeting | None,
    keys: list[str],
    title: str,
    occurred_at: datetime,
) -> MeetingResult | None:
    roots = list(
        (
            await session.execute(
                select(MeetingResult).where(MeetingResult.parent_result_id.is_(None))
            )
        ).scalars()
    )
    candidates: list[MeetingResult] = []
    for item in roots:
        same_calendar = calendar is not None and item.calendar_meeting_id == calendar.id
        calendars_conflict = (
            calendar is not None
            and item.calendar_meeting_id is not None
            and item.calendar_meeting_id != calendar.id
        )
        same_reference = (
            not calendars_conflict
            and bool(keys)
            and references_overlap(keys, item.mts_link_keys)
        )
        same_title = (
            not calendars_conflict
            and bool(title)
            and normalize_meeting_title(item.title) == title
            and abs((item.starts_at - occurred_at).total_seconds()) <= 7 * 86_400
        )
        if same_calendar or same_reference or same_title:
            candidates.append(item)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (item.origin_type == MTS_TRANSCRIPT_ORIGIN, item.starts_at),
        reverse=True,
    )[0]


async def record_email_meeting_result(
    session: AsyncSession,
    event: CommunicationEvent,
    analysis: SemanticAnalysis,
    signal: MeetingResultSignal | None,
    analyzed_at: datetime,
) -> MeetingResult | None:
    if (
        signal is None
        or not signal.detected
        or signal.confidence < MEETING_RESULT_CONFIDENCE
    ):
        return None
    existing = await session.scalar(
        select(MeetingResult).where(MeetingResult.source_event_id == event.id)
    )
    if existing is not None:
        return existing
    duplicate = await session.scalar(
        select(MeetingResult)
        .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
        .where(
            CommunicationEvent.id != event.id,
            CommunicationEvent.content_hash == event.content_hash,
            CommunicationEvent.direction == event.direction,
        )
    )
    if duplicate is not None:
        return None

    keys = mts_link_reference_keys(event.subject, event.body, event.source_url)
    calendar = await _find_calendar_for_email(session, event, signal, keys)
    title_key = normalize_meeting_title(signal.meeting_title or event.subject)
    root = await _find_existing_root(session, calendar, keys, title_key, event.occurred_at)
    urls = find_mts_link_urls(event.subject, event.body, event.source_url)
    item = MeetingResult(
        source_id=event.source_id,
        transcript_id=None,
        event_session_id=None,
        activity_session_id=None,
        source_event_id=event.id,
        calendar_meeting_id=calendar.id if calendar else None,
        parent_result_id=root.id if root else None,
        origin_type=EMAIL_FOLLOWUP_ORIGIN,
        title=(signal.meeting_title or event.subject or "Итоги встречи")[:500],
        starts_at=calendar.starts_at if calendar else event.occurred_at,
        ends_at=calendar.ends_at if calendar else event.occurred_at,
        owner_name=event.author,
        meeting_url=(urls[0] if urls else event.source_url),
        mts_link_keys=keys,
        transcript_status="email",
        evidence=signal.evidence,
        summary=analysis.summary,
        decisions=list(analysis.decisions),
        agreements=list(analysis.agreements),
        analyzed_at=analyzed_at,
    )
    session.add(item)
    await session.flush()
    return item


async def attach_email_results_to_transcript(
    session: AsyncSession,
    transcript: MeetingResult,
) -> int:
    roots = list(
        (
            await session.execute(
                select(MeetingResult).where(
                    MeetingResult.parent_result_id.is_(None),
                    MeetingResult.origin_type == EMAIL_FOLLOWUP_ORIGIN,
                    MeetingResult.id != transcript.id,
                )
            )
        ).scalars()
    )
    linked = 0
    transcript_title = normalize_meeting_title(transcript.title)
    for item in roots:
        same_calendar = (
            transcript.calendar_meeting_id is not None
            and transcript.calendar_meeting_id == item.calendar_meeting_id
        )
        calendars_conflict = (
            transcript.calendar_meeting_id is not None
            and item.calendar_meeting_id is not None
            and transcript.calendar_meeting_id != item.calendar_meeting_id
        )
        same_reference = (
            not calendars_conflict
            and references_overlap(transcript.mts_link_keys, item.mts_link_keys)
        )
        same_title = (
            not calendars_conflict
            and bool(transcript_title)
            and transcript_title == normalize_meeting_title(item.title)
            and abs((transcript.starts_at - item.starts_at).total_seconds()) <= 7 * 86_400
        )
        if same_calendar or same_reference or same_title:
            item.parent_result_id = transcript.id
            linked += 1
    return linked


async def update_meeting_result_analysis(
    session: AsyncSession,
    event: CommunicationEvent,
    analysis: SemanticAnalysis,
    analyzed_at: datetime,
) -> None:
    meeting_result = await session.scalar(
        select(MeetingResult).where(MeetingResult.source_event_id == event.id)
    )
    if meeting_result is None:
        return
    meeting_result.summary = analysis.summary
    meeting_result.decisions = list(analysis.decisions)
    meeting_result.agreements = list(analysis.agreements)
    meeting_result.analyzed_at = analyzed_at
