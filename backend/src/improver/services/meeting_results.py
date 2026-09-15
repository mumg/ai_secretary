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
from improver.services.ollama import (
    MeetingResultSignal,
    MeetingTopicMatch,
    OllamaAnalyzer,
    SemanticAnalysis,
)
from improver.services.text import bounded_text

MEETING_TRANSCRIPT_EVENT_TYPE = "meeting_transcript"
MTS_TRANSCRIPT_ORIGIN = "mts_transcript"
EMAIL_FOLLOWUP_ORIGIN = "email_followup"
MEETING_RESULT_CONFIDENCE = 0.78
MEETING_WINDOW_OVERLAP = 0.80
MEETING_TOPIC_CONFIDENCE = 0.75

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


def closest_completed_meeting(meetings: list[Meeting], occurred_at: datetime) -> Meeting | None:
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
    fallback_summary = next((item.summary for item in [result, *supplements] if item.summary), "")
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


def matching_title(left: str | None, right: str | None) -> bool:
    title = normalize_meeting_title(left)
    return bool(title) and title == normalize_meeting_title(right)


def select_transcript_calendar(
    result: MeetingResult, candidates: list[tuple[Meeting, CommunicationEvent]]
) -> list[tuple[Meeting, CommunicationEvent, float]]:
    """Return ID matches covering at least 80% of the MTS result duration."""
    matches: list[tuple[Meeting, CommunicationEvent, float]] = []
    for meeting, event in candidates:
        keys = meeting.mts_link_keys or mts_link_reference_keys(
            meeting.location, event.body, event.source_url
        )
        duration = max(0.0, (result.ends_at - result.starts_at).total_seconds())
        overlap = max(
            0.0,
            (
                min(result.ends_at, meeting.ends_at) - max(result.starts_at, meeting.starts_at)
            ).total_seconds(),
        )
        ratio = (
            overlap / duration
            if duration
            else float(meeting.starts_at <= result.starts_at < meeting.ends_at)
        )
        if (
            meeting.status != "CANCELLED"
            and references_overlap(result.mts_link_keys or [], keys)
            and ratio >= MEETING_WINDOW_OVERLAP
        ):
            matches.append((meeting, event, ratio))
    return sorted(matches, key=lambda item: (item[2], item[0].starts_at), reverse=True)


def semantic_analysis_from_event(event: CommunicationEvent) -> SemanticAnalysis | None:
    if not event.semantic_summary:
        return None
    return SemanticAnalysis(
        summary=event.semantic_summary,
        thread_summary=event.semantic_summary,
        categories=event.semantic_categories or [],
        keywords=event.semantic_keywords or [],
        people=event.semantic_people or [],
        organizations=event.semantic_organizations or [],
        decisions=event.semantic_decisions or [],
        agreements=event.semantic_agreements or [],
    )


async def select_semantic_transcript_calendar(
    result: MeetingResult,
    analysis: SemanticAnalysis,
    candidates: list[tuple[Meeting, CommunicationEvent]],
    analyzer: OllamaAnalyzer,
) -> tuple[Meeting | None, list[tuple[Meeting, MeetingTopicMatch, float]]]:
    evaluations = []
    for meeting, event, ratio in select_transcript_calendar(result, candidates):
        decision = await analyzer.match_meeting_topic(
            meeting.title,
            bounded_text(event.body or "", 4_000),
            analysis,
        )
        evaluations.append((meeting, decision, ratio))
    accepted = [
        item
        for item in evaluations
        if item[1].matches and item[1].confidence >= MEETING_TOPIC_CONFIDENCE
    ]
    # A reused ID may produce overlapping Outlook windows. Topic confirmation
    # must identify exactly one occurrence; import order is never a tie-breaker.
    return (accepted[0][0] if len(accepted) == 1 else None), evaluations


async def link_result_to_calendar(
    session: AsyncSession,
    meeting_result: MeetingResult,
    *,
    candidates: list[tuple[Meeting, CommunicationEvent]] | None = None,
    analysis: SemanticAnalysis | None = None,
    analyzer: OllamaAnalyzer | None = None,
) -> Meeting | None:
    if candidates is None:
        candidates = await _calendar_candidates(session)
    if analysis is None or analyzer is None:
        meeting = None
        evaluations = []
    else:
        meeting, evaluations = await select_semantic_transcript_calendar(
            meeting_result, analysis, candidates, analyzer
        )
    meeting_result.calendar_meeting_id = meeting.id if meeting else None
    event = await session.get(CommunicationEvent, meeting_result.source_event_id)
    if event is not None:
        event.analysis_result = {
            **(event.analysis_result or {}),
            "calendar_topic_match": {
                "selected_meeting_id": str(meeting.id) if meeting else None,
                "candidates": [
                    {
                        "meeting_id": str(candidate.id),
                        "overlap_ratio": round(ratio, 4),
                        "matches": decision.matches,
                        "confidence": decision.confidence,
                        "evidence": decision.evidence,
                    }
                    for candidate, decision, ratio in evaluations
                ],
            },
        }
    return meeting


def can_attach_result(
    root: MeetingResult,
    calendar_id: object | None,
    keys: list[str],
    title: str,
    occurred_at: datetime,
) -> bool:
    # A confirmed calendar occurrence and room ID identify the meeting. Email
    # arrival time is only a chronology guard, not the meeting time.
    return (
        calendar_id is not None
        and root.calendar_meeting_id == calendar_id
        and root.starts_at <= occurred_at
        and references_overlap(keys or [], root.mts_link_keys or [])
    )


def choose_result_root(candidates: list[MeetingResult]) -> MeetingResult | None:
    if not candidates:
        return None

    def score(item):
        return (item.origin_type == MTS_TRANSCRIPT_ORIGIN, item.starts_at)

    ordered = sorted(candidates, key=score, reverse=True)
    if len(ordered) > 1 and score(ordered[0]) == score(ordered[1]):
        return None
    return ordered[0]


async def _consolidate_calendar_results(
    session: AsyncSession,
    calendar_meeting_id: object,
) -> None:
    rows = list(
        (
            await session.execute(
                select(MeetingResult, CommunicationEvent)
                .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
                .where(MeetingResult.calendar_meeting_id == calendar_meeting_id)
                .order_by(CommunicationEvent.occurred_at, MeetingResult.id)
            )
        ).all()
    )
    roots = [
        item
        for item, _ in rows
        if item.origin_type == MTS_TRANSCRIPT_ORIGIN and item.parent_result_id is None
    ]
    for item, event in rows:
        if item.origin_type != EMAIL_FOLLOWUP_ORIGIN:
            continue
        root = choose_result_root(
            [
                candidate
                for candidate in roots
                if can_attach_result(
                    candidate,
                    calendar_meeting_id,
                    item.mts_link_keys,
                    item.title,
                    event.occurred_at,
                )
            ]
        )
        # Clear stale parents as well, including a parent linked to another calendar.
        item.parent_result_id = root.id if root else None
        if root is None:
            roots.append(item)


async def link_results_to_calendar_meeting(
    session: AsyncSession,
    meeting: Meeting,
    event: CommunicationEvent,
    analyzer: OllamaAnalyzer | None = None,
) -> int:
    # Use the same matching rules regardless of which source was imported first.
    rows = list(
        (
            await session.execute(
                select(MeetingResult, CommunicationEvent).join(
                    CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id
                )
            )
        ).all()
    )
    candidates = await _calendar_candidates(session)
    linked = 0
    affected = {meeting.id}
    for item, source_event in rows:
        previous_id = item.calendar_meeting_id
        if previous_id != meeting.id and not references_overlap(
            item.mts_link_keys or [], meeting.mts_link_keys or []
        ):
            continue
        if item.origin_type == MTS_TRANSCRIPT_ORIGIN:
            result_event = next(row_event for row, row_event in rows if row.id == item.id)
            await link_result_to_calendar(
                session,
                item,
                candidates=candidates,
                analysis=semantic_analysis_from_event(result_event),
                analyzer=analyzer,
            )
        else:
            calendar = await _find_calendar_for_email(
                session,
                source_event,
                MeetingResultSignal(detected=True, confidence=1, meeting_title=item.title),
                item.mts_link_keys,
                candidates=candidates,
            )
            item.calendar_meeting_id = calendar.id if calendar else None
            item.starts_at = calendar.starts_at if calendar else source_event.occurred_at
            item.ends_at = calendar.ends_at if calendar else source_event.occurred_at
        if previous_id != item.calendar_meeting_id:
            item.parent_result_id = None
            affected.update([previous_id, item.calendar_meeting_id])
            linked += 1
    await session.flush()
    for calendar_id in affected - {None}:
        await _consolidate_calendar_results(session, calendar_id)
    return linked


async def _find_calendar_for_email(
    session: AsyncSession,
    event: CommunicationEvent,
    signal: MeetingResultSignal,
    keys: list[str],
    *,
    candidates: list[tuple[Meeting, CommunicationEvent]] | None = None,
) -> Meeting | None:
    if candidates is None:
        candidates = await _calendar_candidates(session)
    # The extracted title can paraphrase the subject (e.g. add "Обсуждение").
    # Keep the original mail subject as evidence for exact calendar matching.
    titles = [event.subject, signal.meeting_title]
    matches = []
    for meeting, calendar_event in candidates:
        if meeting.status == "CANCELLED" or not any(
            matching_title(title, meeting.title) for title in titles
        ):
            continue
        calendar_keys = meeting.mts_link_keys or mts_link_reference_keys(
            meeting.location, calendar_event.body, calendar_event.source_url
        )
        if not references_overlap(keys, calendar_keys):
            continue
        matches.append(meeting)
    # An email arrival time cannot identify an occurrence of a reused room.
    # Keep ambiguous follow-ups standalone until a concrete occurrence is known.
    earliest = event.occurred_at - timedelta(days=45)
    matches = [
        meeting
        for meeting in matches
        if earliest <= meeting.starts_at and meeting.ends_at <= event.occurred_at
    ]
    thread_matches = [
        meeting
        for meeting, calendar_event in candidates
        if meeting in matches
        and event.thread_external_id
        and calendar_event.source_id == event.source_id
        and calendar_event.thread_external_id == event.thread_external_id
    ]
    if thread_matches:
        matches = thread_matches
    return matches[0] if len(matches) == 1 else None


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
    return choose_result_root(
        [
            item
            for item in roots
            if can_attach_result(item, calendar.id if calendar else None, keys, title, occurred_at)
        ]
    )


async def record_email_meeting_result(
    session: AsyncSession,
    event: CommunicationEvent,
    analysis: SemanticAnalysis,
    signal: MeetingResultSignal | None,
    analyzed_at: datetime,
) -> MeetingResult | None:
    if signal is None or not signal.detected or signal.confidence < MEETING_RESULT_CONFIDENCE:
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
    rows = list(
        (
            await session.execute(
                select(MeetingResult, CommunicationEvent)
                .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
                .where(
                    MeetingResult.parent_result_id.is_(None),
                    MeetingResult.origin_type == EMAIL_FOLLOWUP_ORIGIN,
                    MeetingResult.id != transcript.id,
                )
            )
        ).all()
    )
    linked = 0
    for item, event in rows:
        if can_attach_result(
            transcript, item.calendar_meeting_id, item.mts_link_keys, item.title, event.occurred_at
        ):
            # Reparent the whole email group, never leave hidden grandchildren behind.
            children = list(
                (
                    await session.execute(
                        select(MeetingResult).where(MeetingResult.parent_result_id == item.id)
                    )
                ).scalars()
            )
            for child in children:
                child.parent_result_id = None
                if can_attach_result(
                    transcript,
                    child.calendar_meeting_id,
                    child.mts_link_keys,
                    child.title,
                    child.ends_at,
                ):
                    child.parent_result_id = transcript.id
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
