from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import and_, case, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from improver.config import AppConfig
from improver.enums import (
    AnalysisState,
    AttachmentState,
    Direction,
    PrioritySource,
    TaskStatus,
)
from improver.models import (
    Attachment,
    CommunicationEvent,
    ConversationThread,
    Meeting,
    MeetingResult,
    Task,
)
from improver.services.analysis_filters import (
    apply_filtered_index,
    filtered_state,
    match_analysis_filter,
)
from improver.services.assignment import AssignmentSignals, assignment_signals
from improver.services.calendar import BusinessCalendar
from improver.services.documents import SUPPORTED_SUFFIXES, DocumentParserClient
from improver.services.meeting_results import (
    MEETING_TRANSCRIPT_EVENT_TYPE,
    MTS_TRANSCRIPT_ORIGIN,
    record_email_meeting_result,
    update_meeting_result_analysis,
)
from improver.services.meetings import MEETING_EVENT_TYPE, upsert_meeting
from improver.services.notifications import NotificationService
from improver.services.ollama import (
    ExtractedTask,
    MailingSignal,
    OllamaAnalyzer,
    SemanticAnalysis,
    is_ollama_processing_error,
)
from improver.services.plans import rebuild_plan
from improver.services.text import bounded_text, clean_email_body
from improver.services.threads import rebuild_conversation_thread, update_conversation_thread

log = structlog.get_logger()
SEMANTIC_INDEX_VERSION = 2
MAILING_CLASSIFICATION_VERSION = 1
TASK_EXTRACTION_VERSION = 1
MAILING_CONFIDENCE_THRESHOLD = 0.85
ANALYSIS_RETRY_BASE_SECONDS = 30
ANALYSIS_RETRY_MAX_SECONDS = 3_600


def analysis_retry_delay(attempt: int) -> timedelta:
    seconds = min(
        ANALYSIS_RETRY_MAX_SECONDS,
        ANALYSIS_RETRY_BASE_SECONDS * (2 ** min(max(attempt - 1, 0), 7)),
    )
    return timedelta(seconds=seconds)


def _normalized_values(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split()).strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(bounded_text(normalized, 500))
        if len(result) == limit:
            break
    return result


def apply_semantic_analysis(event: CommunicationEvent, analysis: SemanticAnalysis) -> None:
    event.semantic_summary = bounded_text(
        analysis.thread_summary or analysis.summary,
        8_000,
    )
    event.semantic_categories = _normalized_values(analysis.categories, 12)
    event.semantic_keywords = _normalized_values(analysis.keywords, 30)
    event.semantic_people = _normalized_values(analysis.people, 30)
    event.semantic_organizations = _normalized_values(analysis.organizations, 20)
    event.semantic_decisions = _normalized_values(analysis.decisions, 20)
    event.semantic_agreements = _normalized_values(analysis.agreements, 20)
    event.semantic_index = "\n".join(
        value
        for value in [
            event.semantic_summary,
            *event.semantic_categories,
            *event.semantic_keywords,
            *event.semantic_people,
            *event.semantic_organizations,
            *event.semantic_decisions,
            *event.semantic_agreements,
        ]
        if value
    ).casefold()
    event.semantic_version = SEMANTIC_INDEX_VERSION


def apply_mailing_classification(
    event: CommunicationEvent,
    signal: MailingSignal | None,
    analyzed_at: datetime,
) -> bool:
    if event.event_type != "email" or signal is None:
        return False
    event.is_mailing = (
        signal.detected and signal.confidence >= MAILING_CONFIDENCE_THRESHOLD
    )
    event.mailing_confidence = signal.confidence
    event.mailing_kind = signal.kind
    event.mailing_version = MAILING_CLASSIFICATION_VERSION
    event.mailing_analyzed_at = analyzed_at
    return event.is_mailing


def missing_meeting_result_signal_condition():
    """Emails in a transcript-linked calendar thread that predate result detection."""
    calendar_event = aliased(CommunicationEvent)
    return and_(
        CommunicationEvent.event_type == "email",
        CommunicationEvent.is_mailing.is_(False),
        func.coalesce(
            func.jsonb_exists(
                cast(CommunicationEvent.analysis_result, JSONB),
                "meeting_result",
            ),
            False,
        ).is_(False),
        select(1)
        .select_from(MeetingResult)
        .join(Meeting, Meeting.id == MeetingResult.calendar_meeting_id)
        .join(calendar_event, calendar_event.id == Meeting.source_event_id)
        .where(
            MeetingResult.origin_type == MTS_TRANSCRIPT_ORIGIN,
            MeetingResult.parent_result_id.is_(None),
            calendar_event.source_id == CommunicationEvent.source_id,
            calendar_event.thread_external_id == CommunicationEvent.thread_external_id,
            CommunicationEvent.occurred_at >= Meeting.starts_at - timedelta(hours=2),
            CommunicationEvent.occurred_at <= Meeting.starts_at + timedelta(days=30),
        )
        .exists(),
    )


class EventPipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        self.documents = DocumentParserClient(config)
        self.analyzer = OllamaAnalyzer(config)
        self.notifications = NotificationService(config)

    async def _create_task_candidates(
        self,
        session: AsyncSession,
        event: CommunicationEvent,
        candidates: list[ExtractedTask],
        assignment: AssignmentSignals,
    ) -> list[tuple[str, str]]:
        pending_notifications: list[tuple[str, str]] = []
        for candidate in candidates:
            if not assignment.eligible or candidate.assignee == "other":
                continue
            duplicate_query = select(Task.id).where(
                func.lower(Task.title) == candidate.title.lower(),
                Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
            if event.thread_external_id:
                duplicate_query = duplicate_query.join(
                    CommunicationEvent,
                    Task.source_event_id == CommunicationEvent.id,
                ).where(CommunicationEvent.thread_external_id == event.thread_external_id)
            else:
                duplicate_query = duplicate_query.where(Task.source_event_id == event.id)
            if await session.scalar(duplicate_query):
                continue
            auto_create = (
                candidate.assignee == "user"
                and candidate.confidence >= self.config.llm.auto_create_confidence
            )
            task = Task(
                title=candidate.title,
                description=candidate.description,
                status=TaskStatus.NEW if auto_create else TaskStatus.NEEDS_CONFIRMATION,
                priority=candidate.priority,
                priority_source=PrioritySource.LLM,
                due_at=BusinessCalendar(self.config).normalize_due(candidate.due_at),
                source_event_id=event.id,
                evidence=candidate.evidence,
                confidence=candidate.confidence,
                manually_created=False,
            )
            session.add(task)
            await session.flush()
            if auto_create and candidate.priority.value == "CRITICAL":
                notification_type = "CRITICAL_TASK"
            elif auto_create:
                notification_type = "NEW_TASK"
            else:
                notification_type = "TASK_CONFIRMATION_REQUIRED"
            pending_notifications.append((notification_type, str(task.id)))
        return pending_notifications

    async def process_batch(self, session: AsyncSession, now: datetime) -> int:
        result = await session.execute(
            select(CommunicationEvent.id)
            .where(
                CommunicationEvent.analysis_state == AnalysisState.PENDING,
                or_(
                    CommunicationEvent.next_analysis_at.is_(None),
                    CommunicationEvent.next_analysis_at <= now,
                ),
            )
            .order_by(
                case(
                    (CommunicationEvent.event_type == MEETING_EVENT_TYPE, 0),
                    (CommunicationEvent.event_type == MEETING_TRANSCRIPT_EVENT_TYPE, 1),
                    else_=2,
                ),
                case(
                    (
                        CommunicationEvent.event_type == MEETING_TRANSCRIPT_EVENT_TYPE,
                        func.length(CommunicationEvent.body),
                    ),
                    else_=0,
                ),
                # Never let a maintenance reanalysis backlog delay new mail. Existing
                # events retain analysis_model while they are requeued; process those
                # from newest to oldest after all genuinely new events.
                case(
                    (CommunicationEvent.analysis_model.is_(None), 0),
                    else_=1,
                ),
                case(
                    (
                        CommunicationEvent.analysis_model.is_not(None),
                        CommunicationEvent.occurred_at,
                    ),
                    else_=None,
                ).desc(),
                CommunicationEvent.occurred_at,
            )
            .limit(self.config.worker.batch_size)
            .with_for_update(skip_locked=True)
        )
        event_ids = list(result.scalars())
        for event_id in event_ids:
            event_result = await session.execute(
                select(CommunicationEvent)
                .where(
                    CommunicationEvent.id == event_id,
                    CommunicationEvent.analysis_state == AnalysisState.PENDING,
                )
                .options(selectinload(CommunicationEvent.attachments))
            )
            event = event_result.scalar_one_or_none()
            if event is None:
                continue
            await self._process_event(session, event, now)
        return len(event_ids)

    async def _extract_attachments(self, attachments: list[Attachment]) -> list[str]:
        texts: list[str] = []
        for attachment in attachments:
            if attachment.extraction_state == AttachmentState.EXTRACTED:
                if attachment.extracted_text:
                    texts.append(attachment.extracted_text)
                continue
            path = Path(attachment.storage_path)
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                attachment.extraction_state = AttachmentState.UNSUPPORTED
                continue
            try:
                attachment.extracted_text = await self.documents.extract(
                    path, attachment.media_type
                )
                attachment.extraction_state = AttachmentState.EXTRACTED
                if attachment.extracted_text:
                    texts.append(attachment.extracted_text)
            except Exception as exc:  # isolated parser failure must not stop event processing
                attachment.extraction_state = AttachmentState.FAILED
                attachment.extraction_error = str(exc)[:2000]
                log.warning(
                    "attachment_extraction_failed", attachment_id=str(attachment.id), error=str(exc)
                )
        return texts

    async def _thread_tasks(self, session: AsyncSession, event: CommunicationEvent) -> list[Task]:
        if not event.thread_external_id:
            return []
        result = await session.execute(
            select(Task)
            .join(CommunicationEvent, Task.source_event_id == CommunicationEvent.id)
            .where(
                CommunicationEvent.source_id == event.source_id,
                CommunicationEvent.thread_external_id == event.thread_external_id,
                Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
        )
        return list(result.scalars())

    async def _thread_context(
        self, session: AsyncSession, event: CommunicationEvent
    ) -> list[dict[str, str | None]]:
        if not event.thread_external_id:
            return []
        existing_summary = await session.scalar(
            select(ConversationThread.summary).where(
                ConversationThread.source_id == event.source_id,
                ConversationThread.thread_external_id == event.thread_external_id,
                ConversationThread.last_event_at < event.occurred_at,
            )
        )
        result = await session.execute(
            select(CommunicationEvent)
            .where(
                CommunicationEvent.source_id == event.source_id,
                CommunicationEvent.thread_external_id == event.thread_external_id,
                CommunicationEvent.occurred_at < event.occurred_at,
                CommunicationEvent.analysis_state == AnalysisState.COMPLETED,
                CommunicationEvent.is_mailing.is_(False),
            )
            .order_by(CommunicationEvent.occurred_at.desc())
            .limit(6)
        )
        context = [
            {
                "occurred_at": previous.occurred_at.isoformat(),
                "author": previous.author,
                "direction": previous.direction,
                "subject": previous.subject,
                "body": bounded_text(previous.body, 500),
            }
            for previous in reversed(list(result.scalars()))
        ]
        if existing_summary:
            context.insert(
                0,
                {
                    "occurred_at": None,
                    "author": "Сводка предыдущей части цепочки",
                    "direction": None,
                    "subject": "Актуальное резюме цепочки",
                    "body": bounded_text(existing_summary, 4_000),
                },
            )
        return context

    async def _process_event(
        self, session: AsyncSession, event: CommunicationEvent, now: datetime
    ) -> None:
        event_id = event.id
        event.thread_external_id = event.thread_external_id or event.external_id
        if event.event_type == MEETING_EVENT_TYPE:
            event.analysis_state = AnalysisState.PROCESSING
            await session.flush()
            try:
                meeting = await upsert_meeting(session, event)
                event.semantic_summary = bounded_text(event.subject or "Встреча", 8_000)
                event.semantic_categories = ["Встреча"]
                event.semantic_keywords = []
                event.semantic_people = []
                event.semantic_organizations = []
                event.semantic_decisions = []
                event.semantic_agreements = []
                event.semantic_index = None
                event.semantic_version = SEMANTIC_INDEX_VERSION
                event.analysis_state = AnalysisState.COMPLETED
                event.analysis_error = None
                event.next_analysis_at = None
                event.analysis_model = None
                event.analysis_result = {
                    "meeting_id": str(meeting.id) if meeting else None,
                    "calendar_event": True,
                }
                event.analyzed_at = now
                if meeting is not None:
                    calendar = BusinessCalendar(self.config)
                    local_now = now.astimezone(calendar.timezone)
                    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                    day_end = day_start + timedelta(days=1)
                    if meeting.starts_at < day_end and meeting.ends_at > day_start:
                        await rebuild_plan(session, now)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                failed_event = await session.get(CommunicationEvent, event_id)
                if failed_event:
                    failed_event.analysis_state = AnalysisState.FAILED
                    failed_event.analysis_error = str(exc)[:4000]
                    await session.commit()
                log.exception("meeting_processing_failed", event_id=str(event_id))
            return
        filter_match = match_analysis_filter(event, self.config.analysis_filters)
        if filter_match:
            apply_filtered_index(event, filter_match)
            event.analysis_state = filtered_state(filter_match)
            event.analysis_error = None
            event.next_analysis_at = None
            event.analysis_model = None
            event.analysis_result = {
                "skipped": True,
                "filter": {"kind": filter_match.kind, "value": filter_match.value},
            }
            event.analyzed_at = now
            await session.commit()
            log.info(
                "event_analysis_skipped",
                event_id=str(event.id),
                filter_kind=filter_match.kind,
            )
            return
        duplicate_event = await session.scalar(
            select(CommunicationEvent).where(
                CommunicationEvent.id != event.id,
                CommunicationEvent.content_hash == event.content_hash,
                CommunicationEvent.direction == event.direction,
                CommunicationEvent.event_type == event.event_type,
                CommunicationEvent.analysis_state == AnalysisState.COMPLETED,
            )
        )
        if duplicate_event and event.event_type != MEETING_TRANSCRIPT_EVENT_TYPE:
            event.is_mailing = duplicate_event.is_mailing
            event.mailing_confidence = duplicate_event.mailing_confidence
            event.mailing_kind = duplicate_event.mailing_kind
            event.mailing_version = duplicate_event.mailing_version
            event.mailing_analyzed_at = duplicate_event.mailing_analyzed_at
            event.analysis_state = AnalysisState.COMPLETED
            event.analysis_error = None
            event.next_analysis_at = None
            event.analyzed_at = now
            if event.is_mailing:
                await rebuild_conversation_thread(
                    session, event.source_id, event.thread_external_id, now
                )
            else:
                await update_conversation_thread(session, event, None, now)
            await session.commit()
            log.info(
                "duplicate_event_skipped",
                event_id=str(event.id),
                duplicate_of=str(duplicate_event.id),
            )
            return
        event.analysis_state = AnalysisState.PROCESSING
        await session.flush()
        pending_notifications: list[tuple[str, str]] = []
        try:
            attachment_texts = await self._extract_attachments(event.attachments)
            thread_tasks = await self._thread_tasks(session, event)
            conversation_context = await self._thread_context(session, event)
            original_body = event.body
            if event.event_type == "email":
                event.body = clean_email_body(event.body)
            character_budget = max(8_000, self.config.llm.context_length * 3)
            event.body = bounded_text(event.body, character_budget // 2)
            assignment = assignment_signals(event, self.config.identity)
            remaining = character_budget - len(event.body)
            attachment_limit = max(0, remaining // max(1, len(attachment_texts)))
            attachment_texts = [bounded_text(text, attachment_limit) for text in attachment_texts]
            analysis = await self.analyzer.analyze(
                event,
                attachment_texts,
                thread_tasks,
                conversation_context,
                assignment,
                now,
                self.config.server.timezone,
            )
            event.body = original_body
            apply_semantic_analysis(event, analysis)
            is_mailing = apply_mailing_classification(event, analysis.mailing, now)
            task_candidates = [] if is_mailing else analysis.tasks
            if (
                not is_mailing
                and event.event_type == "email"
                and event.direction == Direction.INCOMING
                and assignment.eligible
                and not task_candidates
            ):
                focused = await self.analyzer.extract_tasks(
                    event,
                    attachment_texts,
                    conversation_context,
                    assignment,
                    now,
                    self.config.server.timezone,
                )
                task_candidates = focused.tasks
                analysis.tasks = task_candidates
            pending_notifications.extend(
                await self._create_task_candidates(
                    session, event, task_candidates, assignment
                )
            )

            if event.direction == Direction.OUTGOING and not is_mailing:
                tasks_by_id = {str(task.id): task for task in thread_tasks}
                for completion in analysis.completion_candidates:
                    task = tasks_by_id.get(completion.task_id)
                    if (
                        task
                        and completion.confidence >= self.config.llm.possible_completion_confidence
                    ):
                        task.status = TaskStatus.POSSIBLY_COMPLETED
                        task.evidence = completion.evidence
                        pending_notifications.append(("TASK_POSSIBLY_COMPLETED", str(task.id)))

            event.analysis_state = AnalysisState.COMPLETED
            event.analysis_error = None
            event.next_analysis_at = None
            event.analysis_model = self.config.llm.model
            event.analysis_result = {
                **analysis.model_dump(mode="json"),
                "assignment_signals": assignment.model_dump(),
                "task_extraction_version": TASK_EXTRACTION_VERSION,
            }
            event.analyzed_at = now
            if event.event_type == MEETING_TRANSCRIPT_EVENT_TYPE:
                await update_meeting_result_analysis(session, event, analysis, now)
            else:
                if is_mailing:
                    await rebuild_conversation_thread(
                        session, event.source_id, event.thread_external_id, now
                    )
                else:
                    await update_conversation_thread(session, event, analysis, now)
                if event.event_type == "email" and not is_mailing:
                    await record_email_meeting_result(
                        session,
                        event,
                        analysis,
                        analysis.meeting_result,
                        now,
                    )
            await rebuild_plan(session, now)
            await session.commit()
            for event_type, object_id in pending_notifications:
                await self.notifications.send(session, event_type, object_id)
        except Exception as exc:
            await session.rollback()
            event = await session.get(CommunicationEvent, event_id)
            if event:
                retryable = is_ollama_processing_error(exc)
                if retryable:
                    event.analysis_attempts += 1
                    event.analysis_state = AnalysisState.PENDING
                    event.next_analysis_at = now + analysis_retry_delay(
                        event.analysis_attempts
                    )
                else:
                    event.analysis_state = AnalysisState.FAILED
                    event.next_analysis_at = None
                event.analysis_error = str(exc)[:4000]
                await session.commit()
            if is_ollama_processing_error(exc):
                log.warning(
                    "event_analysis_scheduled_for_retry",
                    event_id=str(event_id),
                    error_type=type(exc).__name__,
                )
            else:
                log.exception("event_analysis_failed", event_id=str(event_id))

    async def process_task_extraction_backfill(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> int:
        already_checked = func.coalesce(
            func.jsonb_exists(
                cast(CommunicationEvent.analysis_result, JSONB),
                "task_extraction_version",
            ),
            False,
        )
        result = await session.execute(
            select(CommunicationEvent)
            .where(
                CommunicationEvent.event_type == "email",
                CommunicationEvent.direction == Direction.INCOMING,
                CommunicationEvent.analysis_state == AnalysisState.COMPLETED,
                CommunicationEvent.is_mailing.is_(False),
                already_checked.is_(False),
                or_(
                    CommunicationEvent.next_analysis_at.is_(None),
                    CommunicationEvent.next_analysis_at <= now,
                ),
                ~exists(
                    select(Task.id).where(Task.source_event_id == CommunicationEvent.id)
                ),
            )
            .options(selectinload(CommunicationEvent.attachments))
            .order_by(CommunicationEvent.occurred_at.desc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        event = result.scalar_one_or_none()
        if event is None:
            return 0
        event_id = event.id
        pending_notifications: list[tuple[str, str]] = []
        try:
            original_body = event.body
            event.body = bounded_text(
                clean_email_body(event.body),
                max(8_000, self.config.llm.context_length * 3) // 2,
            )
            assignment = assignment_signals(event, self.config.identity)
            candidates: list[ExtractedTask] = []
            if assignment.eligible:
                attachment_texts = await self._extract_attachments(event.attachments)
                conversation_context = await self._thread_context(session, event)
                focused = await self.analyzer.extract_tasks(
                    event,
                    attachment_texts,
                    conversation_context,
                    assignment,
                    now,
                    self.config.server.timezone,
                )
                candidates = focused.tasks
                pending_notifications = await self._create_task_candidates(
                    session, event, candidates, assignment
                )
            event.body = original_body
            existing_result = (
                event.analysis_result if isinstance(event.analysis_result, dict) else {}
            )
            stored_tasks = existing_result.get("tasks")
            serialized_tasks = (
                [item.model_dump(mode="json") for item in candidates]
                if assignment.eligible
                else stored_tasks if isinstance(stored_tasks, list) else []
            )
            event.analysis_result = {
                **existing_result,
                "tasks": serialized_tasks,
                "assignment_signals": assignment.model_dump(),
                "task_extraction_version": TASK_EXTRACTION_VERSION,
            }
            event.analysis_error = None
            event.next_analysis_at = None
            if pending_notifications:
                await rebuild_plan(session, now)
            await session.commit()
            for event_type, object_id in pending_notifications:
                await self.notifications.send(session, event_type, object_id)
            return 1
        except Exception as exc:
            await session.rollback()
            failed_event = await session.get(CommunicationEvent, event_id)
            if failed_event is not None:
                if is_ollama_processing_error(exc):
                    failed_event.analysis_attempts += 1
                    failed_event.next_analysis_at = now + analysis_retry_delay(
                        failed_event.analysis_attempts
                    )
                    failed_event.analysis_error = str(exc)[:4000]
                else:
                    failed_event.analysis_result = {
                        **(failed_event.analysis_result or {}),
                        "task_extraction_version": -1,
                    }
                    failed_event.analysis_error = str(exc)[:4000]
                await session.commit()
            if is_ollama_processing_error(exc):
                log.warning(
                    "task_extraction_backfill_scheduled_for_retry",
                    event_id=str(event_id),
                    error_type=type(exc).__name__,
                )
                return 0
            log.exception("task_extraction_backfill_failed", event_id=str(event_id))
            return 1

    async def process_semantic_backfill(
        self,
        session: AsyncSession,
        now: datetime,
        limit: int = 1,
    ) -> int:
        missing_meeting_result_signal = missing_meeting_result_signal_condition()
        result = await session.execute(
            select(CommunicationEvent.id)
            .where(
                CommunicationEvent.analysis_state == AnalysisState.COMPLETED,
                CommunicationEvent.semantic_version >= 0,
                or_(
                    CommunicationEvent.next_analysis_at.is_(None),
                    CommunicationEvent.next_analysis_at <= now,
                ),
                or_(
                    CommunicationEvent.semantic_version < SEMANTIC_INDEX_VERSION,
                    func.nullif(func.btrim(CommunicationEvent.semantic_summary), "").is_(None),
                    missing_meeting_result_signal,
                ),
            )
            .order_by(
                case((missing_meeting_result_signal, 0), else_=1),
                CommunicationEvent.occurred_at.desc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        event_ids = list(result.scalars())
        indexed = 0
        for event_id in event_ids:
            event_result = await session.execute(
                select(CommunicationEvent)
                .where(CommunicationEvent.id == event_id)
                .options(selectinload(CommunicationEvent.attachments))
            )
            event = event_result.scalar_one_or_none()
            if event is None:
                continue
            try:
                attachment_texts = [
                    bounded_text(attachment.extracted_text, 8_000)
                    for attachment in event.attachments
                    if attachment.extracted_text
                ]
                conversation_context = await self._thread_context(session, event)
                original_body = event.body
                body = (
                    clean_email_body(original_body)
                    if event.event_type == "email"
                    else original_body
                )
                event.body = bounded_text(body, max(8_000, self.config.llm.context_length * 2))
                analysis = await self.analyzer.classify_archive(
                    event,
                    attachment_texts,
                    conversation_context,
                    now,
                    self.config.server.timezone,
                )
                event.body = original_body
                apply_semantic_analysis(event, analysis)
                is_mailing = apply_mailing_classification(event, analysis.mailing, now)
                event.analysis_result = {
                    **(event.analysis_result or {}),
                    **analysis.model_dump(mode="json"),
                }
                event.analysis_model = event.analysis_model or self.config.llm.model
                event.analysis_error = None
                event.next_analysis_at = None
                if is_mailing:
                    await rebuild_conversation_thread(
                        session, event.source_id, event.thread_external_id, now
                    )
                else:
                    await update_conversation_thread(session, event, analysis, now)
                if event.event_type == "email" and not is_mailing:
                    await record_email_meeting_result(
                        session,
                        event,
                        analysis,
                        analysis.meeting_result,
                        now,
                    )
                await session.commit()
                indexed += 1
            except Exception as exc:
                await session.rollback()
                failed_event = await session.get(CommunicationEvent, event_id)
                if failed_event:
                    if is_ollama_processing_error(exc):
                        failed_event.analysis_attempts += 1
                        failed_event.next_analysis_at = now + analysis_retry_delay(
                            failed_event.analysis_attempts
                        )
                        failed_event.analysis_error = str(exc)[:4000]
                    else:
                        failed_event.semantic_version = -1
                        failed_event.next_analysis_at = None
                    await session.commit()
                if is_ollama_processing_error(exc):
                    log.warning(
                        "semantic_backfill_scheduled_for_retry",
                        event_id=str(event_id),
                        error_type=type(exc).__name__,
                    )
                else:
                    log.exception(
                        "semantic_backfill_failed",
                        event_id=str(event_id),
                        error=str(exc),
                    )
        return indexed

    async def process_mailing_backfill(
        self,
        session: AsyncSession,
        now: datetime,
        limit: int = 8,
    ) -> int:
        result = await session.execute(
            select(CommunicationEvent)
            .where(
                CommunicationEvent.event_type == "email",
                CommunicationEvent.analysis_state == AnalysisState.COMPLETED,
                CommunicationEvent.mailing_version < MAILING_CLASSIFICATION_VERSION,
                or_(
                    CommunicationEvent.next_analysis_at.is_(None),
                    CommunicationEvent.next_analysis_at <= now,
                ),
            )
            .order_by(CommunicationEvent.occurred_at.desc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars())
        if not events:
            return 0
        event_ids = [event.id for event in events]
        try:
            batch = await self.analyzer.classify_mailings(events)
            decisions = {decision.event_id: decision for decision in batch.decisions}
            missing = 0
            for event in events:
                decision = decisions.get(str(event.id))
                if decision is None:
                    missing += 1
                    signal = MailingSignal(
                        detected=False,
                        confidence=0,
                        kind="uncertain",
                    )
                else:
                    signal = MailingSignal.model_validate(decision.model_dump())
                is_mailing = apply_mailing_classification(event, signal, now)
                event.analysis_result = {
                    **(event.analysis_result or {}),
                    "mailing": signal.model_dump(mode="json"),
                }
                event.analysis_error = None
                event.next_analysis_at = None
                if is_mailing:
                    await rebuild_conversation_thread(
                        session, event.source_id, event.thread_external_id, now
                    )
            await session.commit()
            if missing:
                log.warning("mailing_batch_decisions_missing", count=missing)
            return len(events)
        except Exception as exc:
            await session.rollback()
            if is_ollama_processing_error(exc):
                for event_id in event_ids:
                    failed_event = await session.get(CommunicationEvent, event_id)
                    if failed_event is None:
                        continue
                    failed_event.analysis_attempts += 1
                    failed_event.next_analysis_at = now + analysis_retry_delay(
                        failed_event.analysis_attempts
                    )
                    failed_event.analysis_error = str(exc)[:4000]
                await session.commit()
                log.warning(
                    "mailing_backfill_scheduled_for_retry",
                    count=len(events),
                    error_type=type(exc).__name__,
                )
                return 0
            raise
