from __future__ import annotations

from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from improver.enums import AnalysisState
from improver.models import CommunicationEvent, ConversationThread
from improver.services.email_subjects import (
    comparison_tokens,
    email_thread_headers,
    subject_classification,
    subject_hit_rate,
    subject_key,
    subject_title,
    subject_tokens,
)
from improver.services.llm import OllamaAnalyzer, SemanticAnalysis
from improver.services.subject_identifiers import identifiers_conflict
from improver.services.text import bounded_text

SUBJECT_HIT_THRESHOLD = 0.8
SUBJECT_MATCH_CONFIDENCE = 0.9
MAX_SUBJECT_CANDIDATES = 3
SUBJECT_MATCH_VERSION = 2
log = structlog.get_logger()


async def index_email_subjects(session: AsyncSession) -> int:
    events = list(
        (
            await session.scalars(
                select(CommunicationEvent)
                .where(CommunicationEvent.event_type == "email")
                .options(raiseload("*"))
            )
        ).all()
    )
    for event in events:
        event.subject_key = subject_key(event.subject)
        event.subject_tokens = subject_tokens(event.subject)
        event.raw_headers = email_thread_headers(event.subject, event.raw_headers or {})
    await session.flush()
    return len(events)


async def reconcile_email_thread(
    session: AsyncSession,
    event: CommunicationEvent,
    now: datetime,
    analyzer: OllamaAnalyzer | None = None,
) -> None:
    if event.event_type != "email":
        return
    event.subject_key = subject_key(event.subject)
    event.subject_tokens = subject_tokens(event.subject)
    event.raw_headers = email_thread_headers(event.subject, event.raw_headers or {})
    classification = event.raw_headers["Subject-Token-Classification"]["tokens"]
    if event.subject_key is None:
        return
    await session.flush()
    group = list(
        (
            await session.scalars(
                select(CommunicationEvent)
                .where(
                    CommunicationEvent.source_id == event.source_id,
                    CommunicationEvent.event_type == "email",
                    CommunicationEvent.subject_key == event.subject_key,
                )
                .order_by(CommunicationEvent.occurred_at, CommunicationEvent.id)
                .options(raiseload("*"))
            )
        ).all()
    )
    # A previously resolved exact subject also remembers an approved semantic alias.
    known = next(
        (
            message
            for message in group
            if (message.raw_headers or {}).get("Subject-Thread-Match", {}).get("version")
            == SUBJECT_MATCH_VERSION
            and (message.raw_headers or {}).get("Subject-Thread-Match", {}).get("subject_key")
            == event.subject_key
            and message.thread_external_id
            and message.thread_external_id.startswith("subject:")
        ),
        None,
    )
    key = known.thread_external_id if known else event.subject_key
    audit = {
        "version": SUBJECT_MATCH_VERSION,
        "method": "exact_subject",
        "subject_key": event.subject_key,
    }
    if known:
        audit = (known.raw_headers or {})["Subject-Thread-Match"]
    elif (
        analyzer is not None
        and not event.is_mailing
        and event.analysis_state
        not in {
            AnalysisState.SKIPPED,
            AnalysisState.IGNORED,
        }
    ):
        candidates = await session.execute(
            select(
                CommunicationEvent.id,
                CommunicationEvent.subject,
                CommunicationEvent.thread_external_id,
            )
            .where(
                CommunicationEvent.source_id == event.source_id,
                CommunicationEvent.event_type == "email",
                CommunicationEvent.subject_key != event.subject_key,
                CommunicationEvent.thread_external_id.like("subject:%"),
                CommunicationEvent.occurred_at < event.occurred_at,
                CommunicationEvent.is_mailing.is_(False),
                CommunicationEvent.analysis_state.not_in(
                    [AnalysisState.SKIPPED, AnalysisState.IGNORED]
                ),
            )
            .order_by(CommunicationEvent.occurred_at.desc())
        )
        by_thread = {}
        thread_identifiers = {}
        for candidate_id, candidate_subject, candidate_key in candidates:
            tokens = subject_classification(candidate_subject)["tokens"]
            thread_identifiers.setdefault(candidate_key, []).extend(tokens)
            hit_rate = subject_hit_rate(
                comparison_tokens(classification), comparison_tokens(tokens)
            )
            if hit_rate < SUBJECT_HIT_THRESHOLD:
                continue
            if candidate_key not in by_thread or hit_rate > by_thread[candidate_key][0]:
                by_thread[candidate_key] = (hit_rate, candidate_id)
        conflicting = {
            key
            for key in by_thread
            if identifiers_conflict(classification, thread_identifiers[key])
        }
        if conflicting:
            audit["identifier_conflicts"] = [str(by_thread[key][1]) for key in sorted(conflicting)]
        by_thread = {
            key: candidate for key, candidate in by_thread.items() if key not in conflicting
        }
        if len(by_thread) > MAX_SUBJECT_CANDIDATES:
            audit["method"] = "ambiguous_candidates"
        elif by_thread:
            matches = []
            decisions = []
            uncertain = False
            for candidate_key, (hit_rate, candidate_id) in sorted(
                by_thread.items(), key=lambda item: item[1][0], reverse=True
            ):
                previous = await session.get(CommunicationEvent, candidate_id)
                try:
                    decision = await analyzer.match_email_thread(previous, event)
                except Exception as exc:
                    log.warning("email_thread_match_unavailable", error_type=type(exc).__name__)
                    uncertain = True
                    decisions.append(
                        {
                            "event_id": str(candidate_id),
                            "hit_rate": hit_rate,
                            "error_type": type(exc).__name__,
                        }
                    )
                    continue
                decisions.append(
                    {"event_id": str(candidate_id), "hit_rate": hit_rate, **decision.model_dump()}
                )
                if decision.confidence < SUBJECT_MATCH_CONFIDENCE:
                    uncertain = True
                elif decision.matches:
                    matches.append(candidate_key)
            audit.update(method="separate", candidates=decisions)
            if len(matches) == 1 and not uncertain:
                key = matches[0]
                audit["method"] = "llm_confirmed"
            elif uncertain or len(matches) > 1:
                audit["method"] = "ambiguous"
    old_keys: set[str] = set()
    for message in group:
        old_key = thread_key(message)
        if old_key != key:
            old_keys.add(old_key)
        message.raw_headers = {
            "Original-Thread-Id": old_key,
            **(message.raw_headers or {}),
            "Subject-Thread-Match": audit,
            "Subject-Token-Classification": subject_classification(message.subject),
        }
        message.thread_external_id = key
    await session.flush()
    for old_key in sorted(old_keys):
        await rebuild_conversation_thread(session, event.source_id, old_key, now)
    if old_keys:
        await rebuild_conversation_thread(session, event.source_id, key, now)


def thread_key(event: CommunicationEvent) -> str:
    return (event.thread_external_id or event.external_id).strip()


def thread_title(event: CommunicationEvent) -> str:
    subject = subject_title(event.subject)
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
                CommunicationEvent.event_type.not_in(["meeting_invitation", "meeting_transcript"]),
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
