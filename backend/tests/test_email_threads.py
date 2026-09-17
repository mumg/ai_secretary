from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock

from sqlalchemy import event as sql_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.config import AppConfig, IdentityConfig
from improver.models import Base, CommunicationEvent, CommunicationSource, ConversationThread, Task
from improver.services.assignment import assignment_signals
from improver.services.email_subjects import (
    provider_thread_expression,
    provider_thread_key,
    subject_classification,
    subject_hit_rate,
    subject_key,
    subject_tokens,
)
from improver.services.llm import EmailThreadMatch, ExtractedTask
from improver.services.pipeline import EventPipeline
from improver.services.subject_identifiers import identifiers_conflict
from improver.services.threads import (
    reconcile_email_thread,
    update_conversation_thread,
)

# SQLite drops timezone information, including in aggregate expressions.
NOW = datetime(2026, 9, 17, 10)
SUBJECT = "INC000021715420  - Дополните информацию по заявке"
TITLE = "Дополнить информацию по заявке INC000021715420"


class SubjectKeyTests(TestCase):
    def test_replies_case_whitespace_and_counters_are_normalized(self):
        for subject in [
            SUBJECT,
            f"Re: FW: {SUBJECT.lower()}",
            f" Ответ: Re[2]: Fwd(3):  {SUBJECT.replace(' ', chr(160))} ",
        ]:
            self.assertEqual(subject_key(subject), subject_key(SUBJECT))
        self.assertEqual(subject_key("Re: Ответ: Статус   проекта"), subject_key("статус проекта"))

    def test_different_subjects_and_empty_subjects_are_not_equal(self):
        self.assertNotEqual(subject_key(SUBJECT), subject_key("INC000021715420 - Принята"))
        for subject in [None, "", "   ", "Re: Ответ:"]:
            self.assertIsNone(subject_key(subject))

    def test_tokens_are_sets_and_hit_rate_is_jaccard(self):
        first = subject_tokens("Re: Согласование договора поставки оборудования оборудования")
        second = subject_tokens("Согласование договора поставки нового оборудования")
        self.assertEqual(len(first), 4)
        self.assertEqual(subject_hit_rate(first, second), 0.8)
        self.assertEqual(subject_hit_rate(second, first), 0.8)
        self.assertEqual(subject_hit_rate([], []), 0)
        self.assertTrue(
            identifiers_conflict(
                subject_classification(SUBJECT)["tokens"],
                subject_classification(SUBJECT.replace("420", "421"))["tokens"],
            )
        )
        self.assertEqual(subject_hit_rate(["встреча"], ["встреча", "сегодня"]), 0)


class SubjectPipelineTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://")

        @sql_event.listens_for(self.engine.sync_engine, "connect")
        def unicode_lower(connection, _):
            connection.create_function("lower", 1, lambda value: value.lower() if value else value)

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.session.add_all(
            [
                CommunicationSource(id=source, label=source, source_type="email")
                for source in ["work", "other"]
            ]
        )
        self.config = AppConfig(identity=IdentityConfig(names=["Максим Муратов"]))
        self.pipeline = EventPipeline(self.config)

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def message(self, number, **kwargs):
        values = dict(
            source_id="work",
            source_type="email",
            external_id=str(number),
            thread_external_id=f"exchange-{number}",
            subject=SUBJECT,
            occurred_at=NOW + timedelta(hours=number),
            event_type="email",
            direction="INCOMING",
            analysis_state="COMPLETED",
            content_hash=str(number),
            body="Максим Муратов, дополните информацию по заявке.",
            participants=[{"name": "Максим Муратов", "role": "to"}],
        )
        values.update(kwargs)
        values["subject_key"] = subject_key(values["subject"])
        values["subject_tokens"] = subject_tokens(values["subject"])
        message = CommunicationEvent(**values)
        self.session.add(message)
        await self.session.flush()
        return message

    async def extract(self, message, **kwargs):
        values = dict(title=TITLE, assignee="user", confidence=0.99, evidence=message.body)
        values.update(kwargs)
        return await self.pipeline._create_task_candidates(
            self.session,
            message,
            [ExtractedTask(**values)],
            assignment_signals(message, self.config.identity),
        )

    async def test_reported_exchange_split_updates_one_task_and_one_conversation(self):
        first = await self.message(1)
        reply = await self.message(
            2,
            subject=f"Re: {SUBJECT}",
            direction="OUTGOING",
            thread_external_id=first.thread_external_id,
        )
        latest = await self.message(3)
        for message in [first, reply, latest]:
            await update_conversation_thread(self.session, message, None, NOW)
        self.assertEqual(len(await self.extract(first)), 1)
        task = await self.session.scalar(select(Task))
        task.status = "IN_PROGRESS"
        task.priority = "HIGH"
        task.priority_source = "MANUAL"
        task.due_at = NOW + timedelta(days=1)
        task_id = task.id

        await reconcile_email_thread(self.session, latest, NOW)
        self.assertEqual(
            await self.extract(
                latest, description="Уточнённый запрос", due_at=NOW + timedelta(days=3)
            ),
            [],
        )
        self.assertEqual(await self.session.scalar(select(func.count(Task.id))), 1)
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.status, "IN_PROGRESS")
        self.assertEqual(task.priority_source, "MANUAL")
        self.assertEqual(task.priority, "HIGH")
        self.assertEqual(task.due_at, NOW + timedelta(days=1))
        self.assertEqual(task.description, "Уточнённый запрос")
        self.assertEqual(task.source_event_id, latest.id)
        self.assertEqual(await self.session.scalar(select(func.count(ConversationThread.id))), 1)
        thread = await self.session.scalar(select(ConversationThread))
        self.assertEqual(thread.event_count, 3)
        self.assertEqual(latest.raw_headers["Original-Thread-Id"], "exchange-3")
        context = await self.pipeline._thread_context(self.session, latest)
        self.assertEqual(len(context), 2)
        self.assertEqual(len(await self.pipeline._thread_tasks(self.session, latest)), 1)
        # Replaying the older event must not replace fresh details or notify again.
        self.assertEqual(await self.extract(first, description="Старый запрос"), [])
        self.assertEqual(task.description, "Уточнённый запрос")
        await reconcile_email_thread(self.session, latest, NOW)
        self.assertEqual(thread.event_count, 3)

    async def test_subject_matching_does_not_cross_sources_topics_or_event_types(self):
        target = await self.message(1)
        others = [
            await self.message(2, source_id="other"),
            await self.message(3, subject="INC000021715421 - Дополните информацию"),
            await self.message(4, event_type="meeting_invitation"),
            await self.message(5, subject="INC0000217154209 - Дополните информацию"),
            await self.message(6, subject="INC000021715420 - Принята в работу"),
        ]
        await reconcile_email_thread(self.session, target, NOW)
        for message in others:
            self.assertTrue(message.thread_external_id.startswith("exchange-"))
        await reconcile_email_thread(self.session, others[0], NOW)
        self.assertEqual(len(await self.extract(target)), 1)
        self.assertEqual(len(await self.extract(others[0])), 1)
        self.assertEqual(await self.session.scalar(select(func.count(Task.id))), 2)

    async def test_different_actions_in_same_incident_still_create_separate_tasks(self):
        first = await self.message(1)
        second = await self.message(2)
        await reconcile_email_thread(self.session, first, NOW)
        await self.extract(first)
        self.assertEqual(len(await self.extract(second, title="Проверить доступ")), 1)
        self.assertEqual(await self.session.scalar(select(func.count(Task.id))), 2)

    async def test_fuzzy_match_requires_llm_confirmation_and_remembers_alias(self):
        first = await self.message(1, subject="Согласование договора поставки оборудования")
        second = await self.message(2, subject="Согласование договора поставки нового оборудования")
        await reconcile_email_thread(self.session, first, NOW)
        analyzer = Mock(
            match_email_thread=AsyncMock(
                return_value=EmailThreadMatch(
                    matches=True,
                    confidence=0.95,
                    evidence="Уточнение того же договора",
                )
            )
        )
        await reconcile_email_thread(self.session, second, NOW, analyzer)
        self.assertEqual(second.thread_external_id, first.thread_external_id)
        self.assertEqual(second.raw_headers["Subject-Thread-Match"]["method"], "llm_confirmed")
        third = await self.message(3, subject=f"Re: {second.subject}")
        await reconcile_email_thread(self.session, third, NOW, analyzer)
        self.assertEqual(third.thread_external_id, first.thread_external_id)
        analyzer.match_email_thread.assert_awaited_once()
        self.assertEqual(third.subject_tokens, second.subject_tokens)
        self.assertEqual(provider_thread_key(second), "exchange-2")
        native = await self.session.scalar(
            select(CommunicationEvent.id).where(provider_thread_expression() == "exchange-2")
        )
        self.assertEqual(native, second.id)

    async def test_rejection_uncertainty_and_llm_failure_leave_threads_separate(self):
        for decision in [
            EmailThreadMatch(matches=False, confidence=0.99, evidence="Другой договор"),
            EmailThreadMatch(matches=True, confidence=0.6, evidence="Недостаточно сведений"),
            RuntimeError("unavailable"),
        ]:
            with self.subTest(decision=decision):
                await self.session.rollback()
                # Recreate sources after rollback of the fixture transaction.
                self.session.add(CommunicationSource(id="work", label="Work", source_type="email"))
                first = await self.message(1, subject="Согласование договора поставки оборудования")
                second = await self.message(
                    2, subject="Согласование договора поставки нового оборудования"
                )
                await reconcile_email_thread(self.session, first, NOW)
                call = (
                    AsyncMock(side_effect=decision)
                    if isinstance(decision, Exception)
                    else AsyncMock(return_value=decision)
                )
                await reconcile_email_thread(
                    self.session, second, NOW, Mock(match_email_thread=call)
                )
                self.assertNotEqual(first.thread_external_id, second.thread_external_id)
                call.assert_awaited_once()

    async def test_multiple_confirmed_candidates_are_ambiguous(self):
        subjects = [
            "Согласование договора поставки оборудования нового",
            "Согласование договора поставки оборудования срочно",
            "Согласование договора поставки оборудования",
        ]
        messages = [await self.message(i, subject=subject) for i, subject in enumerate(subjects)]
        for message in messages[:2]:
            await reconcile_email_thread(self.session, message, NOW)
        analyzer = Mock(
            match_email_thread=AsyncMock(
                return_value=EmailThreadMatch(
                    matches=True,
                    confidence=0.99,
                    evidence="Похожее обсуждение",
                )
            )
        )
        await reconcile_email_thread(self.session, messages[-1], NOW, analyzer)
        self.assertEqual(len({m.thread_external_id for m in messages}), 3)
        self.assertEqual(analyzer.match_email_thread.await_count, 2)

    async def test_different_identifiers_and_low_overlap_never_call_llm(self):
        first = await self.message(1)
        await reconcile_email_thread(self.session, first, NOW)
        analyzer = Mock(match_email_thread=AsyncMock())
        for i, subject in enumerate(
            [SUBJECT.replace("420", "421"), "Отчёт по проекту Orion"], start=2
        ):
            message = await self.message(i, subject=subject)
            await reconcile_email_thread(self.session, message, NOW, analyzer)
            self.assertNotEqual(message.thread_external_id, first.thread_external_id)
        analyzer.match_email_thread.assert_not_awaited()

    async def test_empty_subjects_keep_original_threads(self):
        first = await self.message(1, subject=None)
        second = await self.message(2, subject="Re: ")
        for message in [first, second]:
            await reconcile_email_thread(self.session, message, NOW)
        self.assertNotEqual(first.thread_external_id, second.thread_external_id)

    async def test_changed_date_requires_model_instead_of_identifier_veto(self):
        first = await self.message(
            1, subject="Согласование договора поставки оборудования 17.09.2026"
        )
        second = await self.message(
            2, subject="Согласование договора поставки оборудования 18.09.2026"
        )
        await reconcile_email_thread(self.session, first, NOW)
        analyzer = Mock(
            match_email_thread=AsyncMock(
                return_value=EmailThreadMatch(
                    matches=True,
                    confidence=0.95,
                    evidence="Перенесён срок в том же обсуждении",
                )
            )
        )
        await reconcile_email_thread(self.session, second, NOW, analyzer)
        analyzer.match_email_thread.assert_awaited_once()
        self.assertEqual(first.thread_external_id, second.thread_external_id)
        headers = await self.session.scalar(
            select(CommunicationEvent.raw_headers).where(CommunicationEvent.id == second.id)
        )
        classified = headers["Subject-Token-Classification"]["tokens"]
        self.assertEqual(next(t for t in classified if t["token"] == "18.09.2026")["kind"], "date")

    async def test_related_number_change_can_be_confirmed_by_model(self):
        first = await self.message(1, subject="задача 145700 перенос файлов дополнение к 143227")
        second = await self.message(2, subject="задача 145700 перенос файлов дополнение к 143228")
        await reconcile_email_thread(self.session, first, NOW)
        analyzer = Mock(
            match_email_thread=AsyncMock(
                return_value=EmailThreadMatch(
                    matches=True,
                    confidence=0.95,
                    evidence="Продолжение основной задачи 145700",
                )
            )
        )
        await reconcile_email_thread(self.session, second, NOW, analyzer)
        self.assertEqual(first.thread_external_id, second.thread_external_id)
        analyzer.match_email_thread.assert_awaited_once()

    async def test_identifier_conflict_cannot_be_hidden_by_an_intermediate_subject(self):
        suffix = "Согласование договора поставки оборудования сети связи"
        first = await self.message(1, subject=f"Задача 123456 - {suffix}")
        intermediate = await self.message(2, subject=f"Задача - {suffix}")
        conflicting = await self.message(3, subject=f"Задача 123457 - {suffix}")
        analyzer = Mock(
            match_email_thread=AsyncMock(
                return_value=EmailThreadMatch(
                    matches=True,
                    confidence=0.99,
                    evidence="Похожая тема",
                )
            )
        )
        await reconcile_email_thread(self.session, first, NOW)
        await reconcile_email_thread(self.session, intermediate, NOW, analyzer)
        self.assertEqual(first.thread_external_id, intermediate.thread_external_id)
        await reconcile_email_thread(self.session, conflicting, NOW, analyzer)
        self.assertNotEqual(first.thread_external_id, conflicting.thread_external_id)
        self.assertTrue(conflicting.raw_headers["Subject-Thread-Match"]["identifier_conflicts"])
        analyzer.match_email_thread.assert_awaited_once()

    async def test_archive_reindex_splits_legacy_incident_group_by_subject(self):
        from improver.services.subject_reconciliation import reconcile_subject_archive

        accepted = await self.message(1, subject="INC000021715420 - Принята в работу")
        request = await self.message(2)
        reply = await self.message(3, subject=f"Re: {SUBJECT}")
        for message in [accepted, request, reply]:
            message.subject_key = None
            message.subject_tokens = []
            message.raw_headers = {"Original-Thread-Id": message.thread_external_id}
            message.thread_external_id = "incident:INC000021715420"
        await update_conversation_thread(self.session, reply, None, NOW)
        await self.extract(request)
        task = await self.session.scalar(select(Task))
        task_id = task.id
        analyzer = Mock(match_email_thread=AsyncMock())
        result = await reconcile_subject_archive(self.session, analyzer, NOW)
        self.assertEqual(result["emails"], 3)
        self.assertEqual(result["subjects"], 2)
        self.assertEqual(request.thread_external_id, reply.thread_external_id)
        self.assertNotEqual(accepted.thread_external_id, request.thread_external_id)
        self.assertEqual(await self.session.scalar(select(func.count(ConversationThread.id))), 2)
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.source_event_id, request.id)
        self.assertEqual(provider_thread_key(reply), "exchange-3")
        analyzer.match_email_thread.assert_not_awaited()
