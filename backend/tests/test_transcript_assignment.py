import uuid
from datetime import UTC, datetime
from importlib.util import find_spec
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, Mock

from improver.config import AppConfig, IdentityConfig
from improver.models import CommunicationEvent
from improver.services.assignment import assignment_signals, transcript_assignment_verdict
from improver.services.ollama import ExtractedTask
from improver.services.pipeline import EventPipeline


class TranscriptAssignmentTests(TestCase):
    def setUp(self):
        self.identity = IdentityConfig(names=["Максим Муратов", "Максим"])
        self.event = CommunicationEvent(
            event_type="meeting_transcript",
            participants=[
                {"name": "Максим Муратов", "role": "speaker", "external_id": "1"},
                {"name": "Евгения Овчинникова", "role": "speaker", "external_id": "2"},
            ],
        )

    def verdict(self, body, quote, evidence=None):
        self.event.body = body
        return transcript_assignment_verdict(
            quote,
            self.identity,
            self.event,
            assignment_signals(self.event, self.identity),
            evidence or quote,
        )

    def test_foreign_commitment_is_not_assigned_to_attendee(self):
        text = "Я проведу нагрузочное тестирование."
        self.assertEqual(self.verdict("12:18:35 · Евгения Овчинникова\n" + text, text), "other")

    def test_user_commitment_uses_actual_speaker(self):
        text = "Я проверю контракт."
        self.assertEqual(self.verdict("12:18:35 · Максим Муратов\n" + text, text), "user")

    def test_direct_instruction_to_user(self):
        text = "Максим Муратов, проверь контракт."
        self.assertEqual(self.verdict("12:18:35 · Евгения Овчинникова\n" + text, text), "user")

    def test_speaker_header_is_not_assignment(self):
        text = "12:18:35 · Максим Муратов\nСтоимость доставки зависит от маршрута."
        self.assertEqual(self.verdict(text, text), "other")

    def test_user_declining_work_is_not_commitment(self):
        text = "Я не буду проводить тестирование."
        self.assertEqual(self.verdict("12:18:35 · Максим Муратов\n" + text, text), "other")

    def test_paraphrased_or_stitched_evidence_is_unproven(self):
        body = "12:18:35 · Евгения Овчинникова\nБудем стараться, будем нагружать."
        self.assertEqual(self.verdict(body, "Евгения: 'будем нагружать'"), "unproven")

    def test_identity_from_another_task_cannot_be_borrowed(self):
        body = (
            "12:18:35 · Евгения Овчинникова\nЯ проведу тестирование.\n\n"
            "12:19:35 · Максим Муратов\nЯ проверю контракт."
        )
        self.assertEqual(
            self.verdict(body, "Я проверю контракт.", "Я проведу тестирование."), "unproven"
        )

    def test_identical_promise_needs_speaker_disambiguation(self):
        quote = "Я проверю контракт."
        body = (
            "12:18:35 · Евгения Овчинникова\n" + quote + "\n\n12:19:35 · Максим Муратов\n" + quote
        )
        self.assertEqual(self.verdict(body, quote), "uncertain")
        self.assertEqual(self.verdict(body, "12:19:35 · Максим Муратов\n" + quote), "user")

    def test_namesake_does_not_resolve_from_confident_model(self):
        self.event.participants.append({"name": "Максим Сидоров", "role": "speaker"})
        text = "Максим, проверь контракт."
        self.assertEqual(self.verdict("12:18:35 · Евгения Овчинникова\n" + text, text), "uncertain")


class TranscriptPipelineTests(IsolatedAsyncioTestCase):
    async def test_reported_foreign_tasks_generate_neither_tasks_nor_notifications(self):
        config = AppConfig(identity=IdentityConfig(names=["Максим Муратов", "Максим"]))
        pipeline = EventPipeline(config)
        session = Mock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
        event = CommunicationEvent(
            id=uuid.uuid4(),
            event_type="meeting_transcript",
            participants=[{"name": "Максим Муратов", "role": "speaker"}],
            body=(
                "12:16:34 · Михаил Свистуленко\nЯ сегодня приступлю к задаче 43.\n\n"
                "12:16:43 · Нарек Погосян\nЯ сделаю по контракту.\n\n"
                "12:18:35 · Евгения Овчинникова\nБудем стараться, будем нагружать."
            ),
        )
        candidates = [
            ExtractedTask(title=title, evidence=quote, assignee="user", confidence=0.99)
            for title, quote in [
                ("Завершить доработку задачи №43 (Битрикс)", "Я сделаю по контракту."),
                (
                    "Провести интеграционное тестирование",
                    "Евгения: 'если 16 дадут, есть вероятность'",
                ),
                ("Провести нагрузочное тестирование", "Будем стараться, будем нагружать."),
            ]
        ]
        result = await pipeline._create_task_candidates(
            session, event, candidates, assignment_signals(event, config.identity)
        )
        self.assertEqual(result, [])
        session.add.assert_not_called()
        session.scalar.assert_not_called()

    async def test_grounded_user_task_still_created(self):
        config = AppConfig(identity=IdentityConfig(names=["Максим Муратов"]))
        session = Mock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
        event = CommunicationEvent(
            id=uuid.uuid4(),
            event_type="meeting_transcript",
            participants=[{"name": "Максим Муратов", "role": "speaker"}],
            body="12:19:35 · Максим Муратов\nЯ проверю контракт.",
        )
        await EventPipeline(config)._create_task_candidates(
            session,
            event,
            [
                ExtractedTask(
                    title="Проверить контракт",
                    evidence="Я проверю контракт.",
                    assignment_evidence=event.body,
                    assignee="user",
                    confidence=0.99,
                )
            ],
            assignment_signals(event, config.identity),
        )
        self.assertEqual(session.add.call_args.args[0].status, "NEW")


@skipUnless(find_spec("aiosqlite"), "aiosqlite is required for isolated database tests")
class CancelledAssignmentTests(IsolatedAsyncioTestCase):
    async def test_cancelled_task_stays_closed_only_for_its_original_event(self):
        from sqlalchemy import event, func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from improver.models import Base, CommunicationSource, Task

        engine = create_async_engine("sqlite+aiosqlite://")

        @event.listens_for(engine.sync_engine, "connect")
        def unicode_lower(connection, _):
            # SQLite's built-in lower() is ASCII-only; production PostgreSQL isn't.
            connection.create_function("lower", 1, lambda value: value.lower() if value else value)

        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add(CommunicationSource(id="test", label="Test", source_type="email"))
                config = AppConfig(identity=IdentityConfig(names=["Максим Муратов"]))
                events = [
                    CommunicationEvent(
                        source_id="test",
                        source_type="email",
                        external_id=str(i),
                        event_type="email",
                        thread_external_id="same-thread",
                        occurred_at=datetime.now(UTC),
                        content_hash=str(i),
                        participants=[{"name": "Максим Муратов", "role": "to"}],
                        body="Максим Муратов, проверь контракт.",
                    )
                    for i in range(2)
                ]
                session.add_all(events)
                await session.flush()
                session.add(
                    Task(
                        title="Проверить контракт", status="CANCELLED", source_event_id=events[0].id
                    )
                )
                await session.flush()
                candidate = ExtractedTask(
                    title="Проверить контракт",
                    evidence=events[0].body,
                    assignee="user",
                    confidence=0.99,
                )
                pipeline = EventPipeline(config)
                result = await pipeline._create_task_candidates(
                    session, events[0], [candidate], assignment_signals(events[0], config.identity)
                )
                self.assertEqual(result, [])
                self.assertEqual(await session.scalar(select(func.count(Task.id))), 1)
                result = await pipeline._create_task_candidates(
                    session, events[1], [candidate], assignment_signals(events[1], config.identity)
                )
                self.assertEqual(len(result), 1)
                self.assertEqual(await session.scalar(select(func.count(Task.id))), 2)
        finally:
            await engine.dispose()
