from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig
from improver.enums import AnalysisState
from improver.models import (
    Attachment,
    CommunicationEvent,
    CommunicationSource,
    CommunicationSourceTag,
    Task,
)
from improver.schemas import ChatHistoryMessage, ChatReference
from improver.services.calendar import BusinessCalendar
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
            len(token) < 3
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
    match = re.search(
        r"\b(?:от|from|с|with)\s+([^?.,:;!]+)",
        query.casefold(),
        flags=re.UNICODE,
    )
    if not match:
        return []
    tokens = search_tokens(match.group(1))
    return expand_search_terms(tokens[:1])


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


def _snippet(value: str | None, limit: int = 500) -> str:
    normalized = " ".join((value or "").split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def _bounded(value: str | None, limit: int = 12_000) -> str:
    text = value or ""
    return text if len(text) <= limit else text[:limit] + "\n[…текст сокращён…]"


def _match_score(values: list[str | None], tokens: list[str]) -> int:
    haystack = " ".join(value or "" for value in values).casefold()
    return sum(1 for token in tokens if token in haystack)


@dataclass
class RetrievedArchive:
    references: list[ChatReference]
    model_context: list[dict[str, object]]
    candidate_context: list[dict[str, object]]


class ArchiveChatService:
    def __init__(self, session: AsyncSession, config: AppConfig):
        self.session = session
        self.config = config

    async def answer(
        self,
        query: str,
        history: list[ChatHistoryMessage],
        tag_ids: list[uuid.UUID],
    ) -> tuple[str, list[ChatReference]]:
        archive = await self.retrieve(query, tag_ids)
        calendar = BusinessCalendar(self.config)
        analyzer = OllamaAnalyzer(self.config)
        async with ollama_request_slot():
            confirmed_ids = set(
                await analyzer.select_relevant_references(
                    query=query,
                    candidates=archive.candidate_context,
                    now=calendar.now(),
                    timezone_name=self.config.server.timezone,
                )
            )
            confirmed_context = [
                item for item in archive.model_context if item["reference_id"] in confirmed_ids
            ]
            answer = await analyzer.answer_from_archive(
                query=query,
                history=[message.model_dump() for message in history],
                references=confirmed_context,
                now=calendar.now(),
                timezone_name=self.config.server.timezone,
            )
        references = [ref for ref in archive.references if ref.key in confirmed_ids]
        return answer.answer, references

    async def stream_answer(
        self,
        query: str,
        history: list[ChatHistoryMessage],
        tag_ids: list[uuid.UUID],
    ) -> AsyncIterator[dict[str, object]]:
        yield {"type": "status", "message": "Ищу кандидатов в смысловом индексе"}
        archive = await self.retrieve(query, tag_ids)
        yield {
            "type": "status",
            "message": f"Qwen проверяет найденные записи: {len(archive.references)}",
        }
        calendar = BusinessCalendar(self.config)
        analyzer = OllamaAnalyzer(self.config)
        async with ollama_request_slot():
            confirmed_ids = set(
                await analyzer.select_relevant_references(
                    query=query,
                    candidates=archive.candidate_context,
                    now=calendar.now(),
                    timezone_name=self.config.server.timezone,
                )
            )
            confirmed_references = [
                reference for reference in archive.references if reference.key in confirmed_ids
            ]
            confirmed_context = [
                item for item in archive.model_context if item["reference_id"] in confirmed_ids
            ]
            yield {
                "type": "references",
                "message": f"Qwen подтвердил источники: {len(confirmed_references)}",
                "references": [
                    reference.model_dump(mode="json") for reference in confirmed_references
                ],
            }
            async for delta in analyzer.stream_answer_from_archive(
                query=query,
                history=[message.model_dump() for message in history],
                references=confirmed_context,
                now=calendar.now(),
                timezone_name=self.config.server.timezone,
            ):
                yield {
                    "type": "answer_delta",
                    "delta": delta,
                    "references": [],
                }
        yield {
            "type": "complete",
            "references": [
                reference.model_dump(mode="json") for reference in confirmed_references
            ],
        }

    async def retrieve(self, query: str, tag_ids: list[uuid.UUID]) -> RetrievedArchive:
        tokens = expand_search_terms(search_tokens(query))
        required_terms = required_entity_terms(query)
        scope = query_scope(query)
        task_rows = (
            [] if scope == "events" else await self._tasks(tokens, required_terms, tag_ids)
        )
        event_rows = (
            [] if scope == "tasks" else await self._events(tokens, required_terms, tag_ids)
        )
        if tokens and not required_terms and not task_rows and not event_rows:
            task_rows = [] if scope == "events" else await self._tasks([], [], tag_ids)
            event_rows = [] if scope == "tasks" else await self._events([], [], tag_ids)

        references: list[ChatReference] = []
        contexts: list[dict[str, object]] = []
        candidates: list[dict[str, object]] = []
        context_budget = min(max(self.config.llm.context_length * 2, 32_000), 240_000)
        context_size = 0

        for task, source_label, event_date, source_url in task_rows:
            key = f"T{len([r for r in references if r.kind == 'task']) + 1}"
            content = "\n".join(
                part
                for part in [
                    f"Название: {task.title}",
                    f"Описание: {_bounded(task.description)}" if task.description else "",
                    f"Статус: {task.status}",
                    f"Приоритет: {task.priority}",
                    f"Срок: {task.due_at.isoformat()}" if task.due_at else "Срок: не задан",
                    f"Основание: {_bounded(task.evidence, 3_000)}" if task.evidence else "",
                ]
                if part
            )
            if context_size + len(content) > context_budget:
                break
            references.append(
                ChatReference(
                    key=key,
                    kind="task",
                    id=task.id,
                    title=task.title,
                    source_label=source_label,
                    occurred_at=event_date or task.created_at,
                    snippet=_snippet(task.description or task.evidence or task.title),
                    source_url=source_url,
                )
            )
            contexts.append({"reference_id": key, "type": "task", "content": content})
            candidates.append(
                {
                    "reference_id": key,
                    "type": "task",
                    "title": task.title,
                    "summary": _snippet(task.description or task.evidence, 1_500),
                    "status": task.status,
                    "priority": task.priority,
                    "date": (event_date or task.created_at).isoformat(),
                }
            )
            context_size += len(content)

        event_number = 0
        for event, source_label in event_rows:
            event_number += 1
            key = f"E{event_number}"
            attachment_parts = [
                f"Вложение {item.filename}:\n{_bounded(item.extracted_text, 8_000)}"
                for item in event.attachments
                if item.extracted_text
            ]
            content = "\n".join(
                [
                    f"Источник: {source_label or event.source_id}",
                    f"Тип: {event.source_type}/{event.event_type}",
                    f"Цепочка: {event.thread_external_id or event.external_id}",
                    f"Дата: {event.occurred_at.isoformat()}",
                    f"Автор: {event.author or 'не указан'}",
                    f"Тема: {event.subject or 'без темы'}",
                    (
                        "Подготовленный смысловой индекс:\n"
                        f"Резюме: {event.semantic_summary or 'не подготовлено'}\n"
                        f"Категории: {', '.join(event.semantic_categories)}\n"
                        f"Ключевые слова: {', '.join(event.semantic_keywords)}\n"
                        f"Люди: {', '.join(event.semantic_people)}\n"
                        f"Организации: {', '.join(event.semantic_organizations)}\n"
                        f"Решения: {'; '.join(event.semantic_decisions)}\n"
                        f"Договорённости: {'; '.join(event.semantic_agreements)}"
                    ),
                    f"Сообщение:\n{_bounded(event.body)}",
                    *attachment_parts,
                ]
            )
            if context_size + len(content) > context_budget:
                break
            title = event.subject or (
                f"Сообщение от {event.author}" if event.author else f"Событие {event.event_type}"
            )
            references.append(
                ChatReference(
                    key=key,
                    kind="event",
                    id=event.id,
                    title=title,
                    source_label=source_label or event.source_id,
                    occurred_at=event.occurred_at,
                    snippet=_snippet(event.body),
                    source_url=event.source_url,
                )
            )
            contexts.append({"reference_id": key, "type": "communication", "content": content})
            candidates.append(
                {
                    "reference_id": key,
                    "type": event.event_type,
                    "thread_id": event.thread_external_id or event.external_id,
                    "date": event.occurred_at.isoformat(),
                    "title": event.subject,
                    "summary": _snippet(event.semantic_summary or event.body, 800),
                    "categories": event.semantic_categories,
                    "keywords": event.semantic_keywords,
                    "people": event.semantic_people,
                    "organizations": event.semantic_organizations,
                    "decisions": event.semantic_decisions,
                    "agreements": event.semantic_agreements,
                }
            )
            context_size += len(content)

        return RetrievedArchive(
            references=references,
            model_context=contexts,
            candidate_context=candidates,
        )

    async def _tasks(
        self,
        tokens: list[str],
        required_terms: list[str],
        tag_ids: list[uuid.UUID],
    ) -> list[tuple[Task, str | None, datetime | None, str | None]]:
        statement = (
            select(
                Task,
                CommunicationSource.label,
                CommunicationEvent.occurred_at,
                CommunicationEvent.source_url,
            )
            .outerjoin(CommunicationEvent, Task.source_event_id == CommunicationEvent.id)
            .outerjoin(CommunicationSource, CommunicationEvent.source_id == CommunicationSource.id)
        )
        if tag_ids:
            statement = statement.where(
                exists(
                    select(CommunicationSourceTag.source_id).where(
                        CommunicationSourceTag.source_id == CommunicationEvent.source_id,
                        CommunicationSourceTag.tag_id.in_(tag_ids),
                    )
                )
            )
        searchable = [Task.title, Task.description, Task.evidence, Task.status, Task.priority]
        if required_terms:
            statement = statement.where(
                or_(
                    *(
                        column.ilike(f"%{term}%")
                        for column in searchable
                        for term in required_terms
                    )
                )
            )
        if tokens:
            statement = statement.where(
                or_(*(column.ilike(f"%{token}%") for column in searchable for token in tokens))
            )
            query_limit = 100
        else:
            query_limit = 40
        result = await self.session.execute(
            statement.order_by(Task.updated_at.desc()).limit(query_limit)
        )
        rows = list(result.tuples())
        if tokens:
            rows.sort(
                key=lambda row: _match_score(
                    [row[0].title, row[0].description, row[0].evidence], tokens
                ),
                reverse=True,
            )
            rows = rows[:12]
        return rows

    async def _events(
        self,
        tokens: list[str],
        required_terms: list[str],
        tag_ids: list[uuid.UUID],
    ) -> list[tuple[CommunicationEvent, str | None]]:
        semantic_rows = await self._semantic_events(tokens, required_terms, tag_ids)
        if len(semantic_rows) >= 16:
            return semantic_rows[:16]
        original_rows = await self._original_events(tokens, required_terms, tag_ids)
        seen = {row[0].id for row in semantic_rows}
        merged = semantic_rows + [row for row in original_rows if row[0].id not in seen]
        return merged[:16]

    async def _semantic_events(
        self,
        tokens: list[str],
        required_terms: list[str],
        tag_ids: list[uuid.UUID],
    ) -> list[tuple[CommunicationEvent, str | None]]:
        statement = select(CommunicationEvent, CommunicationSource.label).outerjoin(
            CommunicationSource, CommunicationEvent.source_id == CommunicationSource.id
        )
        statement = statement.where(
            CommunicationEvent.semantic_version >= 1,
            CommunicationEvent.analysis_state != AnalysisState.IGNORED,
        )
        if tag_ids:
            statement = statement.where(
                exists(
                    select(CommunicationSourceTag.source_id).where(
                        CommunicationSourceTag.source_id == CommunicationEvent.source_id,
                        CommunicationSourceTag.tag_id.in_(tag_ids),
                    )
                )
            )
        if required_terms:
            statement = statement.where(
                or_(
                    *(
                        CommunicationEvent.semantic_index.ilike(f"%{term}%")
                        for term in required_terms
                    )
                )
            )
        if tokens:
            statement = statement.where(
                or_(
                    *(CommunicationEvent.semantic_index.ilike(f"%{token}%") for token in tokens)
                )
            )
            query_limit = 100
        else:
            query_limit = 24
        result = await self.session.execute(
            statement.order_by(CommunicationEvent.occurred_at.desc()).limit(query_limit)
        )
        rows = list(result.tuples())
        if tokens:
            rows.sort(
                key=lambda row: _match_score([row[0].semantic_index], tokens),
                reverse=True,
            )
        return rows[:16]

    async def _original_events(
        self,
        tokens: list[str],
        required_terms: list[str],
        tag_ids: list[uuid.UUID],
    ) -> list[tuple[CommunicationEvent, str | None]]:
        statement = select(CommunicationEvent, CommunicationSource.label).outerjoin(
            CommunicationSource, CommunicationEvent.source_id == CommunicationSource.id
        )
        statement = statement.where(
            CommunicationEvent.analysis_state != AnalysisState.IGNORED
        )
        if tag_ids:
            statement = statement.where(
                exists(
                    select(CommunicationSourceTag.source_id).where(
                        CommunicationSourceTag.source_id == CommunicationEvent.source_id,
                        CommunicationSourceTag.tag_id.in_(tag_ids),
                    )
                )
            )
        searchable = [
            CommunicationEvent.subject,
            CommunicationEvent.author,
            CommunicationEvent.body,
            CommunicationEvent.event_type,
        ]
        if required_terms:
            required_attachment_match = exists(
                select(Attachment.id).where(
                    Attachment.event_id == CommunicationEvent.id,
                    or_(
                        *(
                            Attachment.extracted_text.ilike(f"%{term}%")
                            for term in required_terms
                        )
                    ),
                )
            )
            statement = statement.where(
                or_(
                    *(
                        column.ilike(f"%{term}%")
                        for column in searchable
                        for term in required_terms
                    ),
                    required_attachment_match,
                )
            )
        if tokens:
            attachment_match = exists(
                select(Attachment.id).where(
                    Attachment.event_id == CommunicationEvent.id,
                    or_(
                        *(Attachment.extracted_text.ilike(f"%{token}%") for token in tokens)
                    ),
                )
            )
            statement = statement.where(
                or_(
                    *(column.ilike(f"%{token}%") for column in searchable for token in tokens),
                    attachment_match,
                )
            )
            query_limit = 100
        else:
            query_limit = 24
        result = await self.session.execute(
            statement.order_by(CommunicationEvent.occurred_at.desc()).limit(query_limit)
        )
        rows = list(result.tuples())
        if tokens:
            rows.sort(
                key=lambda row: _match_score(
                    [
                        row[0].subject,
                        row[0].author,
                        row[0].body,
                        *(attachment.extracted_text for attachment in row[0].attachments),
                    ],
                    tokens,
                ),
                reverse=True,
            )
            rows = rows[:16]
        return rows
