"""Synthetic evidence regression tests; optional isolated PostgreSQL integration run.

Set ARCHIVE_SEARCH_TEST_DATABASE_URL only to a disposable DB migrated to head.
All test inserts are rolled back. Never point this suite at the working archive.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from improver.config import AppConfig
from improver.models import (
    Attachment,
    Base,
    CommunicationEvent,
    CommunicationSource,
    CommunicationSourceTag,
    ConversationThread,
    Tag,
    Task,
)
from improver.schemas import ChatHistoryMessage
from improver.services.archive_chat import ArchiveChatService, resolve_query, search_intent
from improver.services.chat_context import excerpt, fit_records, serialized_size
from improver.services.llm import OllamaAnalyzer

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def message(body="orion budget approved", **kwargs):
    values = dict(
        id=uuid.uuid4(),
        source_id="synthetic",
        source_type="imap",
        external_id=uuid.uuid4().hex,
        event_type="email",
        body=body,
        content_hash=uuid.uuid4().hex,
        occurred_at=NOW,
        analysis_state="COMPLETED",
        semantic_version=0,
    )
    values.update(kwargs)
    return CommunicationEvent(**values)


def attachment(body):
    return Attachment(
        filename="synthetic.txt",
        media_type="text/plain",
        size_bytes=len(body),
        sha256="0" * 64,
        storage_path="/unused",
        extracted_text=body,
    )


class MemorySession:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    def get_bind(self):
        return self.session.get_bind()

    def add_all(self, rows):
        self.session.add_all(rows)

    async def flush(self):
        self.session.flush()


class RetrievalTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.raw = Session(self.engine)
        self.session = MemorySession(self.raw)
        await self.initialize()

    async def initialize(self):
        self.now_patch = patch("improver.services.calendar.BusinessCalendar.now", return_value=NOW)
        self.now_patch.start()
        self.addCleanup(self.now_patch.stop)
        self.service = ArchiveChatService(self.session, AppConfig())
        await self.insert(
            CommunicationSource(id="synthetic", label="Synthetic", source_type="imap")
        )

    async def asyncTearDown(self):
        self.raw.close()
        self.engine.dispose()

    async def insert(self, *rows):
        self.session.add_all(rows)
        await self.session.flush()

    async def retrieve(self, query="Письма orion budget", tags=None):
        return await self.service.retrieve(query, tags or [])

    async def test_old_exact_match_survives_recent_partial_matches(self):
        target = message(occurred_at=NOW - timedelta(days=200))
        await self.insert(
            target,
            *(message("orion newsletter", occurred_at=NOW - timedelta(days=i)) for i in range(101)),
        )
        archive = await self.retrieve()
        self.assertEqual(archive.references[0].id, target.id)

    async def test_indexed_partial_matches_do_not_hide_original(self):
        target = message()
        await self.insert(
            target,
            *(
                message("newsletter", semantic_version=1, semantic_index="orion newsletter")
                for _ in range(16)
            ),
        )
        self.assertEqual((await self.retrieve()).references[0].id, target.id)

    async def test_attachment_and_sender_evidence_reach_both_stages(self):
        target = message("general report", author="alice@example.com")
        target.attachments = [attachment("x" * 13_000 + " orion budget approved")]
        await self.insert(target)
        archive = await self.retrieve("Письма от alice@example.com orion")
        for context in (archive.candidate_context, archive.model_context):
            text = json.dumps(context)
            self.assertIn("alice@example.com", text)
            self.assertIn("orion budget approved", text)

    async def test_big_message_does_not_hide_later_small_message(self):
        big = message("orion " + "x" * 12_000)
        big.attachments = [attachment("x" * 8000) for _ in range(3)]
        small = message("orion", occurred_at=NOW - timedelta(days=1))
        await self.insert(big, small)
        self.assertEqual(
            {r.id for r in (await self.retrieve("Письма orion")).references}, {big.id, small.id}
        )

    async def test_tail_of_original_is_in_answer_context(self):
        await self.insert(message("x" * 13_000 + " orion budget approved"))
        self.assertIn("orion budget approved", json.dumps((await self.retrieve()).model_context))

    async def test_overdue_filter_excludes_closed_and_future_tasks(self):
        target = Task(
            id=uuid.uuid4(),
            title="old obligation",
            due_at=NOW - timedelta(days=2),
            status="NEW",
            updated_at=NOW - timedelta(days=200),
        )
        await self.insert(
            target,
            Task(title="future", due_at=NOW + timedelta(days=1)),
            *(
                Task(title="closed", status="COMPLETED", due_at=NOW - timedelta(days=1))
                for _ in range(41)
            ),
        )
        archive = await self.retrieve("Какие задачи просрочены?")
        self.assertEqual([r.id for r in archive.references], [target.id])
        self.assertIn("due_at", archive.candidate_context[0])

    async def test_previous_week_uses_half_open_date_range(self):
        old = message(occurred_at=NOW.replace(hour=0) - timedelta(days=7))
        current = message(occurred_at=NOW.replace(hour=0))
        earlier = message(occurred_at=NOW.replace(hour=0) - timedelta(days=8))
        await self.insert(old, current, earlier)
        self.assertEqual(
            [r.id for r in (await self.retrieve("Письма за прошлую неделю")).references], [old.id]
        )

    async def test_task_can_be_found_by_source_author(self):
        source = message("general", author="alice@example.com")
        await self.insert(source)
        target = Task(title="prepare budget", source_event_id=source.id)
        await self.insert(target)
        archive = await self.retrieve("Задачи от alice@example.com")
        self.assertEqual([r.id for r in archive.references], [target.id])

    async def test_recent_thread_update_is_included_without_topic_words(self):
        original = message(thread_external_id="discussion", occurred_at=NOW - timedelta(days=2))
        update = message("move deadline to friday", thread_external_id="discussion")
        await self.insert(original, update)
        await self.insert(
            ConversationThread(
                source_id="synthetic",
                source_type="imap",
                thread_external_id="discussion",
                title="orion",
                summary="approved, then postponed",
                first_event_at=original.occurred_at,
                last_event_at=NOW,
                latest_event_id=update.id,
            )
        )
        archive = await self.retrieve("Письма orion")
        self.assertEqual({r.id for r in archive.references}, {original.id, update.id})
        self.assertIn("move deadline to friday", json.dumps(archive.model_context))
        self.assertIn("approved, then postponed", json.dumps(archive.candidate_context))

    async def test_summary_can_find_latest_message_without_keywords(self):
        latest = message("approved", thread_external_id="discussion")
        await self.insert(latest)
        await self.insert(
            ConversationThread(
                source_id="synthetic",
                source_type="imap",
                thread_external_id="discussion",
                summary="orion budget",
                first_event_at=NOW,
                last_event_at=NOW,
                latest_event_id=latest.id,
            )
        )
        self.assertEqual([r.id for r in (await self.retrieve()).references], [latest.id])

    async def test_historical_query_does_not_include_future_thread_summary(self):
        original = message(thread_external_id="discussion", occurred_at=NOW - timedelta(days=1))
        await self.insert(original)
        await self.insert(
            ConversationThread(
                source_id="synthetic",
                source_type="imap",
                thread_external_id="discussion",
                summary="future private update",
                first_event_at=original.occurred_at,
                last_event_at=NOW,
                summarized_at=NOW,
                latest_event_id=original.id,
            )
        )
        archive = await self.retrieve("Письма orion вчера")
        self.assertNotIn("future private update", json.dumps(archive.model_context))

    async def test_thread_neighbors_respect_date_and_source_boundaries(self):
        await self.insert(CommunicationSource(id="other", label="Other", source_type="imap"))
        tag = Tag(id=uuid.uuid4(), name="allowed", normalized_name="allowed")
        await self.insert(tag)
        await self.insert(CommunicationSourceTag(source_id="synthetic", tag_id=tag.id))
        target = message(thread_external_id="shared", occurred_at=NOW - timedelta(days=1))
        await self.insert(
            target,
            message("orion budget", source_id="other", thread_external_id="shared"),
            message(
                "future update", thread_external_id="shared", occurred_at=NOW + timedelta(days=1)
            ),
            message("ignored", thread_external_id="shared", analysis_state="IGNORED"),
        )
        archive = await self.retrieve("Письма orion вчера", [tag.id])
        self.assertEqual([r.id for r in archive.references], [target.id])

    async def test_no_match_does_not_return_unrelated_recent_records(self):
        await self.insert(message("unrelated"))
        with patch("improver.services.archive_chat.OllamaAnalyzer") as analyzer:
            answer, refs = await self.service.answer("Письма missingproject", [], [])
        self.assertEqual(refs, [])
        self.assertIn("не найдено", answer)
        analyzer.assert_not_called()

    async def test_short_project_code_is_searchable(self):
        target = message("HR approved")
        await self.insert(target)
        self.assertEqual([r.id for r in (await self.retrieve("Письма HR")).references], [target.id])


@skipUnless(os.getenv("ARCHIVE_SEARCH_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class PostgresRetrievalTests(RetrievalTests):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["ARCHIVE_SEARCH_TEST_DATABASE_URL"])
        self.session = AsyncSession(self.engine, expire_on_commit=False)
        await self.initialize()

    async def asyncTearDown(self):
        await self.session.rollback()
        await self.session.close()
        await self.engine.dispose()

    async def test_russian_morphology_in_original_and_task(self):
        original = message("согласование договоров", semantic_version=0)
        task = Task(title="согласование договоров")
        await self.insert(original, task)
        archive = await self.retrieve("договор")
        self.assertEqual({r.id for r in archive.references}, {original.id, task.id})


class SearchContextTests(TestCase):
    def test_followup_inherits_subject_but_independent_question_does_not(self):
        history = [ChatHistoryMessage(role="user", content="Что решили по Orion?")]
        self.assertIn("Orion", resolve_query("А кто отвечает?", history))
        self.assertEqual(resolve_query("Письма от Петрова", history), "Письма от Петрова")

    def test_date_phrase_is_not_a_person_and_status_is_not_a_keyword(self):
        intent = search_intent("Письма с прошлой недели", NOW)
        self.assertFalse(intent.entity)
        self.assertFalse(intent.groups)
        self.assertEqual(search_intent("Незавершённые задачи", NOW).statuses[0], "NEW")

    def test_excerpt_preserves_separate_matches(self):
        value = "x" * 2000 + "orion approved " + "y" * 3000 + "budget tomorrow"
        result = excerpt(value, ["orion", "budget"], 500)
        self.assertIn("orion", result)
        self.assertIn("budget", result)
        self.assertLessEqual(len(result), 500)

    def test_packing_preserves_small_records_dates_and_evidence(self):
        records = [
            {
                "reference_id": "T1",
                "due_at": NOW.isoformat(),
                "summary": "x" * 5000,
                "evidence": "x" * 1000 + "orion approved",
            },
            {"reference_id": "E1", "evidence": "orion revised"},
        ]
        packed = fit_records(records, 700, "orion")
        self.assertLessEqual(serialized_size(packed), 700)
        self.assertEqual({r["reference_id"] for r in packed}, {"T1", "E1"})
        self.assertEqual(packed[0]["due_at"], NOW.isoformat())
        self.assertIn("orion", packed[0]["evidence"])

    def test_complete_payload_is_bounded_with_long_history(self):
        for context in (4096, 16384):
            config = AppConfig()
            config.llm.context_length = context
            payload = OllamaAnalyzer(config)._archive_chat_payload(
                query="orion",
                history=[{"role": "user", "content": "я" * 8000}] * 12,
                references=[
                    {"reference_id": f"E{i}", "evidence": "orion " + "я" * 8000} for i in range(16)
                ],
                now=NOW,
                timezone_name="UTC",
                stream=False,
            )
            size = sum(len(m["content"].encode("utf-8")) for m in payload["messages"])
            self.assertLessEqual(size + payload["options"]["num_predict"] + 128, context)
            self.assertTrue(json.loads(payload["messages"][1]["content"])["archive_context"])


class OllamaChatBudgetTests(IsolatedAsyncioTestCase):
    async def test_selection_uses_bounded_evidence_and_rejects_unknown_ids(self):
        import httpx

        analyzer = OllamaAnalyzer(AppConfig())
        candidates = [
            {
                "reference_id": f"E{i}",
                "author": "alice@example.com",
                "evidence": "x" * 5000 + " orion approved",
            }
            for i in range(28)
        ]

        async def respond(payload):
            envelope = json.loads(payload["messages"][1]["content"])
            self.assertLess(serialized_size(envelope), payload["options"]["num_ctx"])
            self.assertTrue(envelope["candidates"])
            self.assertIn("orion", envelope["candidates"][0]["evidence"])
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": json.dumps(
                            {"reference_ids": [envelope["candidates"][0]["reference_id"], "E99999"]}
                        )
                    }
                },
                request=httpx.Request("POST", "http://synthetic/api/chat"),
            )

        analyzer._post_chat = AsyncMock(side_effect=respond)
        refs = await analyzer.select_relevant_references("orion", candidates, NOW, "UTC")
        self.assertEqual(refs, ["E0"])

    async def test_answer_only_returns_citations_to_sent_sources(self):
        import httpx

        analyzer = OllamaAnalyzer(AppConfig())
        analyzer._post_chat = AsyncMock(
            return_value=httpx.Response(
                200,
                json={"message": {"content": "Approved [E1]. Invalid [E999]."}},
                request=httpx.Request("POST", "http://synthetic/api/chat"),
            )
        )
        result = await analyzer.answer_from_archive(
            "orion", [], [{"reference_id": "E1", "evidence": "orion approved"}], NOW, "UTC"
        )
        self.assertEqual(result.used_reference_ids, ["E1"])


@skipUnless(os.getenv("ARCHIVE_SEARCH_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class OllamaSlotTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["ARCHIVE_SEARCH_TEST_DATABASE_URL"])
        self.engine_patch = patch("improver.db.engine", self.engine)
        with patch(
            "improver.config.DatabaseConfig.resolved_url",
            return_value=os.environ["ARCHIVE_SEARCH_TEST_DATABASE_URL"],
        ):
            self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_slot_is_released_on_cancellation_and_supports_nested_calls(self):
        import asyncio

        from improver.services.llm import ollama_request_slot

        entered = asyncio.Event()

        async def hold():
            async with ollama_request_slot(interactive=True):
                async with ollama_request_slot():
                    entered.set()
                    await asyncio.Event().wait()

        task = asyncio.create_task(hold())
        try:
            await asyncio.wait_for(entered.wait(), 3)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        async with asyncio.timeout(3):
            async with ollama_request_slot(interactive=True):
                pass

    async def test_ready_chat_gets_slot_before_background(self):
        import asyncio

        from sqlalchemy import delete

        from improver.models import ChatRequest
        from improver.services.llm import ollama_request_slot

        request_id = uuid.uuid4()
        async with AsyncSession(self.engine) as session:
            session.add(ChatRequest(id=request_id, query="synthetic", status="PENDING"))
            await session.commit()
        background_entered = asyncio.Event()

        async def background():
            async with ollama_request_slot():
                background_entered.set()

        task = asyncio.create_task(background())
        try:
            await asyncio.sleep(0.05)
            async with asyncio.timeout(3):
                async with ollama_request_slot(interactive=True):
                    self.assertFalse(background_entered.is_set())
        finally:
            async with AsyncSession(self.engine) as session:
                await session.execute(delete(ChatRequest).where(ChatRequest.id == request_id))
                await session.commit()
            try:
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
        self.assertTrue(background_entered.is_set())

    async def test_oversized_question_is_failed_without_endless_retries(self):
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from improver.models import ChatRequest
        from improver.services.chat_context import ChatContextError
        from improver.services.chat_queue import process_next_chat_request

        request_id = uuid.uuid4()
        async with AsyncSession(self.engine) as session:
            session.add(ChatRequest(id=request_id, query="synthetic"))
            await session.commit()
        try:
            with (
                patch(
                    "improver.db.SessionFactory",
                    async_sessionmaker(self.engine, expire_on_commit=False),
                ),
                patch(
                    "improver.services.archive_chat.ArchiveChatService.answer",
                    new=AsyncMock(side_effect=ChatContextError("synthetic context limit")),
                ),
            ):
                self.assertTrue(await process_next_chat_request(AppConfig()))
            async with AsyncSession(self.engine) as session:
                request = await session.get(ChatRequest, request_id)
                self.assertEqual(request.status, "FAILED")
                self.assertIsNone(request.next_attempt_at)
        finally:
            async with AsyncSession(self.engine) as session:
                await session.execute(delete(ChatRequest).where(ChatRequest.id == request_id))
                await session.commit()
