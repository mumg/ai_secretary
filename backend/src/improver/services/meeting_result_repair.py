"""Rebuild derived meeting links without changing source content or LLM analyses."""

from sqlalchemy import select

from improver.models import CommunicationEvent, MeetingContext, MeetingResult
from improver.services.meeting_results import (
    EMAIL_FOLLOWUP_ORIGIN,
    MTS_TRANSCRIPT_ORIGIN,
    _calendar_candidates,
    _find_calendar_for_email,
    can_attach_result,
    choose_result_root,
    link_result_to_calendar,
    semantic_analysis_from_event,
)
from improver.services.mts_link import mts_link_reference_keys
from improver.services.ollama import MeetingResultSignal, OllamaAnalyzer


async def repair_meeting_result_links(
    session, analyzer: OllamaAnalyzer | None = None
) -> dict[str, int]:
    """Caller stops writers and owns the transaction; only aggregate counts are returned."""
    calendars = await _calendar_candidates(session)
    changed_calendars = set()
    for meeting, event in calendars:
        keys = mts_link_reference_keys(meeting.location, event.body, event.source_url)
        if keys != meeting.mts_link_keys:
            changed_calendars.add(meeting.id)
            meeting.mts_link_keys = keys
    rows = list(
        (
            await session.execute(
                select(MeetingResult, CommunicationEvent)
                .join(CommunicationEvent, CommunicationEvent.id == MeetingResult.source_event_id)
                .order_by(CommunicationEvent.occurred_at, MeetingResult.id)
            )
        ).all()
    )

    def state(item):
        return (item.calendar_meeting_id, item.parent_result_id, item.starts_at, item.ends_at)

    before = {item.id: state(item) for item, _ in rows}
    changed_keys = 0
    for item, event in rows:
        if item.origin_type == MTS_TRANSCRIPT_ORIGIN:
            metadata = (event.raw_headers or {}).get("MTS-Link", {})
            keys = mts_link_reference_keys(
                item.meeting_url,
                known_ids=(
                    item.event_session_id,
                    item.activity_session_id,
                    metadata.get("event_id"),
                ),
            )
        else:
            keys = mts_link_reference_keys(event.subject, event.body, event.source_url)
        changed_keys += keys != item.mts_link_keys
        item.mts_link_keys = keys
        item.parent_result_id = None
        if item.origin_type == MTS_TRANSCRIPT_ORIGIN:
            calendar = await link_result_to_calendar(
                session,
                item,
                candidates=calendars,
                analysis=semantic_analysis_from_event(event),
                analyzer=analyzer,
            )
        else:
            calendar = await _find_calendar_for_email(
                session,
                event,
                MeetingResultSignal(detected=True, confidence=1, meeting_title=item.title),
                keys,
                candidates=calendars,
            )
            item.starts_at = calendar.starts_at if calendar else event.occurred_at
            item.ends_at = calendar.ends_at if calendar else event.occurred_at
        item.calendar_meeting_id = calendar.id if calendar else None

    roots = [item for item, _ in rows if item.origin_type == MTS_TRANSCRIPT_ORIGIN]
    for item, event in rows:
        if item.origin_type != EMAIL_FOLLOWUP_ORIGIN:
            continue
        root = choose_result_root(
            [
                candidate
                for candidate in roots
                if can_attach_result(
                    candidate,
                    item.calendar_meeting_id,
                    item.mts_link_keys,
                    item.title,
                    event.occurred_at,
                )
            ]
        )
        if root:
            item.parent_result_id = root.id
        else:
            roots.append(item)

    changed = {item.id for item, _ in rows if state(item) != before[item.id]}
    affected_ids = set(changed)
    for item, _ in rows:
        if item.id in changed:
            affected_ids.update([before[item.id][1], item.parent_result_id])
            changed_calendars.update([before[item.id][0], item.calendar_meeting_id])
    event_ids = {str(item.source_event_id) for item, _ in rows if item.id in affected_ids}
    invalidated = 0
    for context in (await session.execute(select(MeetingContext))).scalars():
        if context.meeting_id not in changed_calendars and not any(
            str(ref.get("id")) in event_ids for ref in context.references or []
        ):
            continue
        context.status = "PENDING" if context.requested_at else "NOT_REQUESTED"
        context.summary, context.references = None, []
        context.input_fingerprint = context.generation = context.generated_at = None
        context.started_at = context.next_refresh_at = context.notify_after = context.error = None
        invalidated += 1
    await session.flush()
    return {
        "results_checked": len(rows),
        "result_keys_corrected": changed_keys,
        "result_links_corrected": len(changed),
        "contexts_invalidated": invalidated,
    }
