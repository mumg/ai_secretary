from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Literal

import structlog
from sqlalchemy import String, and_, case, cast, exists, func, literal, or_, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload, selectinload

from improver.config import AppConfig
from improver.enums import AnalysisState, TaskStatus
from improver.models import (
    Attachment,
    CommunicationEvent,
    CommunicationSource,
    CommunicationSourceTag,
    ConversationThread,
    Task,
)
from improver.schemas import ChatHistoryMessage, ChatReference
from improver.services.calendar import BusinessCalendar
from improver.services.chat_context import excerpt
from improver.services.full_text_search import full_text_match
from improver.services.ollama import OllamaAnalyzer, ollama_request_slot

STOP_WORDS = {
    "the",
    "and",
    "for",
    "what",
    "with",
    "где",
    "для",
    "есть",
    "или",
    "как",
    "какие",
    "когда",
    "кто",
    "мне",
    "мои",
    "мой",
    "надо",
    "отправителя",
    "про",
    "что",
    "это",
    "покажи",
    "найди",
    "расскажи",
    "чем",
    "какой",
    "кого",
    "который",
    "которые",
    "было",
    "были",
    "его",
    "она",
    "они",
    "все",
    "отвечает",
    "срок",
    "сроки",
    "договорились",
}

DOMAIN_WORD_PREFIXES = (
    "письм",
    "получал",
    "поручен",
    "переписк",
    "сообщен",
    "задач",
)

CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)

RUSSIAN_SEARCH_SUFFIXES = (
    "ились",
    "ались",
    "ями",
    "ами",
    "ого",
    "ему",
    "ому",
    "ими",
    "ыми",
    "ую",
    "юю",
    "ом",
    "ем",
    "ым",
    "им",
    "ах",
    "ях",
    "ой",
    "ей",
    "а",
    "я",
    "ы",
    "и",
    "у",
    "ю",
    "е",
)


def search_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[\w@.-]+", query.casefold(), flags=re.UNICODE):
        token = token.strip("._-@")
        if (
            len(token) < 2
            or token
            in {"от", "по", "на", "мы", "он", "об", "за", "до", "из", "со", "не", "ли", "же"}
            or token in STOP_WORDS
            or token.startswith(DOMAIN_WORD_PREFIXES)
            or token in tokens
        ):
            continue
        tokens.append(token)
        if len(tokens) == 10:
            break
    return tokens


def expand_search_terms(tokens: list[str]) -> list[str]:
    terms: list[str] = []
    for token in tokens:
        aliases = [token]
        for suffix in RUSSIAN_SEARCH_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                aliases.append(token[: -len(suffix)])
                break
        aliases.extend(
            alias.translate(CYRILLIC_TO_LATIN)
            for alias in list(aliases)
            if alias.translate(CYRILLIC_TO_LATIN) != alias
        )
        for alias in aliases:
            if alias and alias not in terms:
                terms.append(alias)
    return terms


def required_entity_terms(query: str) -> list[str]:
    match = re.search(r"\b(?:от|from|с|with)\s+([\w@.+-]+)", query.casefold())
    if not match:
        return []
    token = match.group(1).rstrip(".")
    if (
        token.startswith(
            (
                "прошл",
                "последн",
                "начал",
                "конц",
                "понедель",
                "вторник",
                "сред",
                "четверг",
                "пятниц",
                "суббот",
                "воскрес",
                "утра",
            )
        )
        or token[0].isdigit()
    ):
        return []
    return expand_search_terms(search_tokens(token)[:1])


def query_scope(query: str) -> Literal["all", "tasks", "events"]:
    normalized = query.casefold()
    mentions_events = bool(
        re.search(r"\b(?:писем|письм|сообщен|переписк|встреч|расшифров)", normalized)
    )
    mentions_tasks = bool(re.search(r"\b(?:задач|поручен)", normalized))
    if mentions_events and not mentions_tasks:
        return "events"
    if mentions_tasks and not mentions_events:
        return "tasks"
    return "all"


def _match_score(values: list[str | None], tokens: list[str]) -> int:
    haystack = " ".join(value or "" for value in values).casefold()
    return sum(1 for token in tokens if token in haystack)


@dataclass
class SearchIntent:
    query: str
    groups: list[list[str]]
    entity: list[str]
    scope: str
    start: datetime | None = None
    end: datetime | None = None
    statuses: tuple[str, ...] = ()
    overdue: bool = False
    correspondence_only: bool = False

    @property
    def terms(self) -> list[str]:
        return list(dict.fromkeys(term for group in self.groups for term in group))


def resolve_query(query: str, history: list[ChatHistoryMessage]) -> str:
    """Carry the subject of an explicit follow-up without mixing independent questions."""
    followup = re.search(
        r"^(?:а\b|кто отвечает|когда\b|какой срок)|\b(?:этому|него|нему|там|этой|этого)\b",
        query.casefold(),
    )
    if not followup:
        return query
    previous = [m.content for m in history if m.role == "user"]
    if not previous:
        return query
    # Walk through follow-ups only, stopping at the most recent independent subject.
    context = []
    for value in reversed(previous[-3:]):
        context.insert(0, value[:500])
        if not re.search(r"^(?:а\b|кто отвечает|когда\b|какой срок)", value.casefold()):
            break
    return "\n".join([*context, query])


def search_intent(query: str, now: datetime) -> SearchIntent:
    text = query.casefold()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end = None
    patterns = [
        (
            r"(?:с|за|на|в)?\s*прошл\w*\s+недел\w*",
            day - timedelta(days=day.weekday() + 7),
            day - timedelta(days=day.weekday()),
        ),
        (
            r"(?:за|на|в)?\s*(?:эт\w*|текущ\w*)\s+недел\w*",
            day - timedelta(days=day.weekday()),
            day + timedelta(days=7 - day.weekday()),
        ),
        (r"\bвчера\b", day - timedelta(days=1), day),
        (r"\bсегодня\b", day, day + timedelta(days=1)),
        (r"\bзавтра\b", day + timedelta(days=1), day + timedelta(days=2)),
    ]
    for pattern, left, right in patterns:
        if re.search(pattern, text):
            start, end = left, right
            text = re.sub(pattern, " ", text)
            break
    recent = re.search(r"(?:за\s+)?последни[ех]\s+(\d{1,3})\s+дн\w*", text)
    if recent:
        start, end = now - timedelta(days=int(recent.group(1))), now
        text = text[: recent.start()] + " " + text[recent.end() :]
    overdue = bool(re.search(r"просроч\w*", text))
    active = (
        TaskStatus.NEW,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_CONFIRMATION,
        TaskStatus.POSSIBLY_COMPLETED,
    )
    statuses = ()
    if overdue or re.search(
        r"незаверш\w*|невыполн\w*|не\s+(?:выполн\w*|заверш\w*)|активн\w*|открыт\w*", text
    ):
        statuses = active
    elif re.search(r"выполн\w*|заверш\w*", text):
        statuses = (TaskStatus.COMPLETED,)
    elif re.search(r"отмен[её]н\w*", text):
        statuses = (TaskStatus.CANCELLED,)
    text = re.sub(
        r"просроч\w*|незаверш\w*|невыполн\w*|не\s+(?:выполн\w*|заверш\w*)|"
        r"активн\w*|открыт\w*|выполн\w*|заверш\w*|отмен[её]н\w*",
        " ",
        text,
    )
    return SearchIntent(
        query,
        [expand_search_terms([t]) for t in search_tokens(text)],
        required_entity_terms(text),
        query_scope(query),
        start,
        end,
        statuses,
        overdue,
    )


@dataclass
class RetrievedArchive:
    references: list[ChatReference]
    model_context: list[dict[str, object]]
    candidate_context: list[dict[str, object]]


log = structlog.get_logger()
EMPTY_ANSWER = (
    "По этому запросу подходящих источников не найдено. Уточните проект, участника или период."
)


def _contains(column, term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


class ArchiveChatService:
    def __init__(self, session: AsyncSession, config: AppConfig):
        self.session = session
        self.config = config

    @property
    def postgres(self) -> bool:
        return self.session.get_bind().dialect.name == "postgresql"

    def _match(self, table: str, columns: list, terms: list[str]):
        if self.postgres:
            # Each alias is quoted, so addresses and hyphens remain search data.
            return full_text_match(table, " OR ".join(f'"{term}"' for term in terms))
        return or_(*(_contains(column, term) for column in columns for term in terms))

    def _tags(self, tag_ids):
        if not tag_ids:
            return literal(True)
        return exists(
            select(CommunicationSourceTag.source_id).where(
                CommunicationSourceTag.source_id == CommunicationEvent.source_id,
                CommunicationSourceTag.tag_id.in_(tag_ids),
            )
        )

    def _entity(self, terms):
        return or_(
            *(
                _contains(column, term)
                for column in (
                    CommunicationEvent.author,
                    cast(CommunicationEvent.participants, String),
                    CommunicationEvent.semantic_index,
                )
                for term in terms
            )
        )

    async def answer(
        self, query: str, history: list[ChatHistoryMessage], tag_ids: list[uuid.UUID]
    ) -> tuple[str, list[ChatReference]]:
        resolved = resolve_query(query, history)
        archive = await self.retrieve(resolved, tag_ids)
        if not archive.references:
            return EMPTY_ANSWER, []
        calendar = BusinessCalendar(self.config)
        analyzer = OllamaAnalyzer(self.config)
        waiting = monotonic()
        async with ollama_request_slot(interactive=True):
            log.info("archive_chat_slot", wait_ms=round((monotonic() - waiting) * 1000))
            started = monotonic()
            confirmed_ids = set(
                await analyzer.select_relevant_references(
                    query=resolved,
                    candidates=archive.candidate_context,
                    now=calendar.now(),
                    timezone_name=self.config.server.timezone,
                )
            )
            log.info(
                "archive_chat_selection",
                duration_ms=round((monotonic() - started) * 1000),
                candidates=len(archive.references),
                selected=len(confirmed_ids),
            )
            if not confirmed_ids:
                return EMPTY_ANSWER, []
            contexts = [
                item for item in archive.model_context if item["reference_id"] in confirmed_ids
            ]
            started = monotonic()
            answer = await analyzer.answer_from_archive(
                query=query,
                history=[m.model_dump() for m in history],
                references=contexts,
                now=calendar.now(),
                timezone_name=self.config.server.timezone,
            )
            log.info("archive_chat_answer", duration_ms=round((monotonic() - started) * 1000))
        used = set(answer.used_reference_ids) & confirmed_ids
        return answer.answer, [ref for ref in archive.references if ref.key in used]

    async def stream_answer(
        self, query: str, history: list[ChatHistoryMessage], tag_ids: list[uuid.UUID]
    ) -> AsyncIterator[dict[str, object]]:
        yield {"type": "status", "message": "Ищу сообщения, задачи и договорённости"}
        resolved = resolve_query(query, history)
        archive = await self.retrieve(resolved, tag_ids)
        if not archive.references:
            yield {"type": "answer_delta", "delta": EMPTY_ANSWER, "references": []}
            yield {"type": "complete", "references": []}
            return
        calendar = BusinessCalendar(self.config)
        analyzer = OllamaAnalyzer(self.config)
        async with ollama_request_slot(interactive=True):
            yield {"type": "status", "message": "Qwen проверяет найденные источники"}
            confirmed_ids = set(
                await analyzer.select_relevant_references(
                    query=resolved,
                    candidates=archive.candidate_context,
                    now=calendar.now(),
                    timezone_name=self.config.server.timezone,
                )
            )
            if not confirmed_ids:
                yield {"type": "answer_delta", "delta": EMPTY_ANSWER, "references": []}
                yield {"type": "complete", "references": []}
                return
            contexts = [
                item for item in archive.model_context if item["reference_id"] in confirmed_ids
            ]
            refs = [ref for ref in archive.references if ref.key in confirmed_ids]
            yield {"type": "references", "references": [r.model_dump(mode="json") for r in refs]}
            answer = ""
            async for delta in analyzer.stream_answer_from_archive(
                query=query,
                history=[m.model_dump() for m in history],
                references=contexts,
                now=calendar.now(),
                timezone_name=self.config.server.timezone,
            ):
                answer += delta
                yield {"type": "answer_delta", "delta": delta, "references": []}
        used = set(re.findall(r"\b[TE]\d+\b", answer.upper()))
        yield {
            "type": "complete",
            "references": [r.model_dump(mode="json") for r in refs if r.key in used],
        }

    async def retrieve(
        self,
        query: str,
        tag_ids: list[uuid.UUID],
        *,
        before: datetime | None = None,
        literal_topic: bool = False,
    ) -> RetrievedArchive:
        started = monotonic()
        if self.postgres:
            # Search selectivity varies greatly. Generic prepared plans regressed sharply
            # after repeated queries on the synthetic archive; scope this to the transaction.
            await self.session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
        now = BusinessCalendar(self.config).now()
        # A calendar title is data: "планы на завтра" must not filter out past evidence.
        intent = (
            SearchIntent(
                query,
                [expand_search_terms([t]) for t in search_tokens(query)],
                [],
                "events",
                correspondence_only=True,
            )
            if literal_topic
            else search_intent(query, now)
        )
        if before is not None:
            intent.end = min(intent.end, before) if intent.end else before
        tasks = [] if intent.scope == "events" else await self._tasks(intent, tag_ids, now)
        events = [] if intent.scope == "tasks" else await self._events(intent, tag_ids)
        if intent.end:
            # A current thread summary may describe revisions outside a historical query.
            events = [
                (event, label, summary, date)
                if date is not None
                and (date if date.tzinfo else date.replace(tzinfo=intent.end.tzinfo)) < intent.end
                else (event, label, None, None)
                for event, label, summary, date in events
            ]
        references, contexts, candidates = [], [], []
        for task, label, date, url, author in tasks:
            key = f"T{len(references) + 1}"
            evidence = excerpt(task.evidence, intent.terms, 800)
            description = excerpt(task.description, intent.terms, 1200)
            card = {
                "reference_id": key,
                "type": "task",
                "title": task.title,
                "summary": description,
                "evidence": evidence,
                "status": task.status,
                "priority": task.priority,
                "author": author,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "date": (date or task.created_at).isoformat(),
            }
            references.append(
                ChatReference(
                    key=key,
                    kind="task",
                    id=task.id,
                    title=task.title,
                    source_label=label,
                    occurred_at=date or task.created_at,
                    snippet=excerpt(task.evidence or task.description, intent.terms, 500),
                    source_url=url,
                )
            )
            contexts.append(dict(card))
            candidates.append(card)
        for index, (event, label, thread_summary, summary_date) in enumerate(events, 1):
            key = f"E{index}"
            attachments = sorted(
                event.attachments,
                key=lambda a: _match_score([a.extracted_text], intent.terms),
                reverse=True,
            )
            fragments = [
                f"{a.filename}: {excerpt(a.extracted_text, intent.terms, 900)}"
                for a in attachments[:3]
                if a.extracted_text
            ]
            evidence = excerpt(event.body, intent.terms, 1400)
            if fragments:
                evidence = "\n".join([*fragments, evidence])
            card = {
                "reference_id": key,
                "type": event.event_type,
                "thread_id": event.thread_external_id or event.external_id,
                "date": event.occurred_at.isoformat(),
                "title": event.subject,
                "author": event.author,
                "summary": excerpt(event.semantic_summary, intent.terms, 600),
                "evidence": evidence,
            }
            # Include compact structured facts missing from short bodies.
            card["summary"] = excerpt(
                "; ".join(
                    filter(
                        None,
                        [
                            event.semantic_summary,
                            *event.semantic_decisions,
                            *event.semantic_agreements,
                            thread_summary,
                        ],
                    )
                ),
                intent.terms,
                800,
            )
            card["participants"] = excerpt(
                json.dumps(event.participants, ensure_ascii=False), intent.terms, 300
            )
            title = event.subject or f"Сообщение от {event.author or 'неизвестного автора'}"
            references.append(
                ChatReference(
                    key=key,
                    kind="event",
                    id=event.id,
                    title=title,
                    source_label=label or event.source_id,
                    occurred_at=event.occurred_at,
                    snippet=excerpt(evidence, intent.terms, 500),
                    source_url=event.source_url,
                )
            )
            card["thread_summary_date"] = summary_date.isoformat() if summary_date else None
            contexts.append(dict(card))
            candidates.append(card)

        # Alternate record types so the model budget is not consumed by tasks alone.
        def interleave(items):
            task_items = [item for item in items if item["reference_id"].startswith("T")]
            event_items = [item for item in items if item["reference_id"].startswith("E")]
            result = []
            while task_items or event_items:
                if task_items:
                    result.append(task_items.pop(0))
                if event_items:
                    result.append(event_items.pop(0))
            return result

        log.info(
            "archive_chat_retrieval",
            duration_ms=round((monotonic() - started) * 1000),
            tasks=len(tasks),
            events=len(events),
            groups=len(intent.groups),
        )
        return RetrievedArchive(references, interleave(contexts), interleave(candidates))

    async def _tasks(self, intent, tag_ids, now):
        statement = (
            select(
                Task,
                CommunicationSource.label,
                CommunicationEvent.occurred_at,
                CommunicationEvent.source_url,
                CommunicationEvent.author,
            )
            .outerjoin(CommunicationEvent, Task.source_event_id == CommunicationEvent.id)
            .outerjoin(CommunicationSource, CommunicationEvent.source_id == CommunicationSource.id)
            .options(raiseload("*"))
            .where(self._tags(tag_ids))
        )
        if intent.statuses:
            statement = statement.where(Task.status.in_(intent.statuses))
        if intent.overdue:
            statement = statement.where(Task.due_at < now)
        if intent.start:
            statement = statement.where(Task.due_at >= intent.start, Task.due_at < intent.end)
        if intent.entity:
            statement = statement.where(
                or_(
                    self._entity(intent.entity),
                    *(
                        _contains(c, term)
                        for c in (Task.title, Task.description, Task.evidence)
                        for term in intent.entity
                    ),
                )
            )
        matches = [
            or_(
                self._match("tasks", [Task.title, Task.description, Task.evidence], group),
                *(_contains(CommunicationEvent.author, term) for term in group),
            )
            for group in intent.groups
        ]
        if matches:
            statement = statement.where(or_(*matches)).order_by(
                sum(case((m, 1), else_=0) for m in matches).desc()
            )
        if intent.overdue or intent.start:
            statement = statement.order_by(Task.due_at.asc())
        result = await self.session.execute(
            statement.order_by(Task.updated_at.desc(), Task.id).limit(12)
        )
        return list(result.tuples())

    def _event_statement(self):
        return (
            select(
                CommunicationEvent,
                CommunicationSource.label,
                ConversationThread.summary,
                ConversationThread.summarized_at,
            )
            .outerjoin(CommunicationSource, CommunicationEvent.source_id == CommunicationSource.id)
            .outerjoin(
                ConversationThread,
                and_(
                    ConversationThread.source_id == CommunicationEvent.source_id,
                    ConversationThread.thread_external_id
                    == func.coalesce(
                        CommunicationEvent.thread_external_id, CommunicationEvent.external_id
                    ),
                ),
            )
            .options(raiseload("*"), selectinload(CommunicationEvent.attachments))
        )

    async def _events(self, intent, tag_ids):
        conditions = [
            CommunicationEvent.analysis_state != AnalysisState.IGNORED,
            self._tags(tag_ids),
        ]
        if intent.correspondence_only:
            conditions.extend(
                [
                    CommunicationEvent.event_type.not_in(
                        ["meeting_invitation", "meeting_cancellation"]
                    ),
                    CommunicationEvent.is_mailing.is_(False),
                ]
            )
        statement = self._event_statement().where(*conditions)
        if intent.start:
            statement = statement.where(CommunicationEvent.occurred_at >= intent.start)
        if intent.end:
            statement = statement.where(CommunicationEvent.occurred_at < intent.end)
        if intent.entity:
            statement = statement.where(self._entity(intent.entity))
        matches = []
        for group in intent.groups:
            attachment_match = exists(
                select(Attachment.id).where(
                    Attachment.event_id == CommunicationEvent.id,
                    self._match(
                        "attachments", [Attachment.extracted_text, Attachment.filename], group
                    ),
                )
            )
            event_match = self._match(
                "communication_events",
                [
                    CommunicationEvent.subject,
                    CommunicationEvent.body,
                    CommunicationEvent.author,
                    CommunicationEvent.semantic_index,
                    cast(CommunicationEvent.participants, String),
                ],
                group,
            )
            thread_match = and_(
                ConversationThread.latest_event_id == CommunicationEvent.id,
                self._match(
                    "conversation_threads",
                    [
                        ConversationThread.title,
                        ConversationThread.summary,
                        cast(ConversationThread.participants, String),
                    ],
                    group,
                ),
            )
            # Prepared semantic text retains substring matching for transliterated aliases.
            semantic_match = or_(
                *(_contains(CommunicationEvent.semantic_index, term) for term in group)
            )
            matches.append(or_(event_match, semantic_match, attachment_match, thread_match))
        if matches:
            if self.postgres:
                # Independent indexed channels avoid a correlated attachment lookup for
                # every archive row. Only their union needs the more detailed ranking.
                event_ids = (
                    select(CommunicationEvent.id)
                    .where(
                        or_(
                            self._match("communication_events", [], intent.terms),
                            *(
                                _contains(CommunicationEvent.semantic_index, t)
                                for t in intent.terms
                            ),
                        )
                    )
                    .correlate(None)
                )
                attachment_ids = select(Attachment.event_id).where(
                    self._match("attachments", [], intent.terms)
                )
                thread_ids = select(ConversationThread.latest_event_id).where(
                    ConversationThread.latest_event_id.is_not(None),
                    self._match("conversation_threads", [], intent.terms),
                )
                statement = statement.where(
                    CommunicationEvent.id.in_(union_all(event_ids, attachment_ids, thread_ids))
                )
            else:
                statement = statement.where(or_(*matches))
            statement = statement.order_by(sum(case((m, 1), else_=0) for m in matches).desc())
        result = await self.session.execute(
            statement.order_by(CommunicationEvent.occurred_at.desc(), CommunicationEvent.id).limit(
                16
            )
        )
        rows = list(result.tuples())
        # Preserve recent revisions of a matched discussion even without topic keywords.
        thread_keys = list(
            dict.fromkeys(
                (r[0].source_id, r[0].thread_external_id) for r in rows if r[0].thread_external_id
            )
        )[:8]
        if not thread_keys:
            return rows
        thread_conditions = [
            and_(
                CommunicationEvent.source_id == source,
                CommunicationEvent.thread_external_id == thread,
            )
            for source, thread in thread_keys
        ]
        ranked = select(
            CommunicationEvent.id.label("id"),
            func.row_number()
            .over(
                partition_by=(CommunicationEvent.source_id, CommunicationEvent.thread_external_id),
                order_by=(CommunicationEvent.occurred_at.desc(), CommunicationEvent.id),
            )
            .label("position"),
        ).where(
            or_(*thread_conditions),
            *conditions,
        )
        if intent.start:
            ranked = ranked.where(CommunicationEvent.occurred_at >= intent.start)
        if intent.end:
            ranked = ranked.where(CommunicationEvent.occurred_at < intent.end)
        recent = ranked.subquery()
        result = await self.session.execute(
            self._event_statement()
            .join(recent, recent.c.id == CommunicationEvent.id)
            .where(recent.c.position <= 2)
            .order_by(CommunicationEvent.occurred_at.desc(), CommunicationEvent.id)
        )
        neighbors = list(result.tuples())
        ordered, seen = [], set()
        for row in rows:
            for candidate in [
                row,
                *(
                    n
                    for n in neighbors
                    if n[0].source_id == row[0].source_id
                    and n[0].thread_external_id == row[0].thread_external_id
                ),
            ]:
                if candidate[0].id not in seen:
                    ordered.append(candidate)
                    seen.add(candidate[0].id)
        return ordered
