import uuid
from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from improver.models import CommunicationEvent
from improver.services.llm import MailingSignal
from improver.services.pipeline import (
    apply_mailing_classification,
    missing_meeting_result_signal_condition,
)


class PipelineQueryTests(TestCase):
    def test_high_confidence_mailing_is_excluded_from_threads(self) -> None:
        event = CommunicationEvent(event_type="email")
        signal = MailingSignal(
            detected=True,
            confidence=0.91,
            kind="newsletter",
        )

        detected = apply_mailing_classification(
            event,
            signal,
            datetime.now(UTC),
        )

        self.assertTrue(detected)
        self.assertTrue(event.is_mailing)
        self.assertEqual(event.mailing_version, 1)

    def test_low_confidence_mailing_remains_visible(self) -> None:
        event = CommunicationEvent(event_type="email")
        signal = MailingSignal(
            detected=True,
            confidence=0.6,
            kind="uncertain",
        )

        detected = apply_mailing_classification(
            event,
            signal,
            datetime.now(UTC),
        )

        self.assertFalse(detected)
        self.assertFalse(event.is_mailing)

    def test_meeting_result_backfill_condition_compiles_for_postgres(self) -> None:
        statement = select(CommunicationEvent.id).where(missing_meeting_result_signal_condition())

        sql = str(statement.compile(dialect=postgresql.dialect()))

        self.assertIn("EXISTS (SELECT", sql)
        self.assertIn("jsonb_exists", sql)


class ContextualTaskAssignmentTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import AsyncMock, Mock

        from improver.config import AppConfig, IdentityConfig
        from improver.services.pipeline import EventPipeline

        self.config = AppConfig(
            identity=IdentityConfig(names=["Иван Петров"], addresses=["ivan@example.test"])
        )
        self.pipeline = EventPipeline(self.config)
        self.session = Mock()
        self.session.scalar = AsyncMock(return_value=None)
        self.session.flush = AsyncMock()
        self.event = CommunicationEvent(
            id=uuid.uuid4(),
            event_type="email",
            body="Иван, подготовь договор.",
            author="manager@example.test",
            participants=[
                {"name": "Иван Петров", "address": "ivan@example.test"},
                {"name": "Иван Сидоров", "address": "other@example.test"},
            ],
        )

    async def create(self, **values):
        from improver.services.assignment import assignment_signals
        from improver.services.llm import ExtractedTask

        context = values.pop("context", [])
        candidate = ExtractedTask(
            title="Подготовить договор",
            assignee="user",
            confidence=0.99,
            priority="NORMAL",
            evidence=self.event.body,
            **values,
        )
        signals = assignment_signals(self.event, self.config.identity, context)
        await self.pipeline._create_task_candidates(
            self.session, self.event, [candidate], signals, context
        )
        return self.session.add.call_args.args[0] if self.session.add.called else None

    async def test_high_confidence_does_not_resolve_shared_name(self):
        self.assertEqual((await self.create()).status, "NEEDS_CONFIRMATION")

    async def test_full_name_in_direct_instruction_resolves_namesake(self):
        self.event.body = "Иван Петров, подготовь договор."
        self.assertEqual((await self.create()).status, "NEW")

    async def test_original_context_quote_can_resolve_person(self):
        quote = "Иван Петров отвечает за подготовку договора."
        task = await self.create(
            assignment_evidence=quote,
            context=[
                {"occurred_at": "2026-09-14", "author": "manager@example.test", "body": quote}
            ],
        )
        self.assertEqual(task.status, "NEW")

    async def test_own_previous_commitment_can_resolve_person(self):
        quote = "Я возьму подготовку договора."
        task = await self.create(
            assignment_evidence=quote,
            context=[
                {"occurred_at": "2026-09-14", "author": "Иван <ivan@example.test>", "body": quote}
            ],
        )
        self.assertEqual(task.status, "NEW")

    async def test_other_namesake_commitment_does_not_resolve_user(self):
        quote = "Я возьму подготовку договора."
        task = await self.create(
            assignment_evidence=quote,
            context=[
                {"occurred_at": "2026-09-14", "author": "Иван <other@example.test>", "body": quote}
            ],
        )
        self.assertEqual(task.status, "NEEDS_CONFIRMATION")

    async def test_invented_resolution_quote_cannot_auto_assign(self):
        task = await self.create(assignment_evidence="Иван Петров отвечает за договор.")
        self.assertEqual(task.status, "NEEDS_CONFIRMATION")

    async def test_summary_alone_cannot_auto_assign(self):
        quote = "Иван Петров отвечает за договор."
        task = await self.create(
            assignment_evidence=quote, context=[{"occurred_at": None, "body": quote}]
        )
        self.assertEqual(task.status, "NEEDS_CONFIRMATION")

    async def test_explicit_foreign_address_does_not_create_user_task(self):
        self.assertIsNone(await self.create(assignee_address="other@example.test"))

    async def test_high_importance_email_forces_high_task_priority(self):
        from improver.enums import PrioritySource, TaskPriority

        self.event.raw_headers = {"Importance": "high"}

        task = await self.create()

        self.assertEqual(task.priority, TaskPriority.HIGH)
        self.assertEqual(task.priority_source, PrioritySource.SOURCE)

    async def test_explicit_foreign_owner_is_not_even_a_confirmation_task(self):
        self.event.body = "Подготовить договор — отв. Сидоров А."
        self.assertIsNone(await self.create())
        self.session.flush.assert_not_called()

    async def test_uncertain_candidate_with_foreign_owner_is_excluded(self):
        from improver.services.assignment import assignment_signals
        from improver.services.llm import ExtractedTask

        self.event.body = "Подготовить договор — отв. Сидоров А."
        notifications = await self.pipeline._create_task_candidates(
            self.session,
            self.event,
            [
                ExtractedTask(
                    title="Подготовить договор",
                    assignee="uncertain",
                    confidence=0.8,
                    evidence=self.event.body,
                )
            ],
            assignment_signals(self.event, self.config.identity),
        )
        self.assertEqual(notifications, [])
        self.session.add.assert_not_called()

    async def test_surname_initial_resolves_shared_first_name(self):
        self.event.body = "Подготовить договор — отв. Петров И."
        self.assertEqual((await self.create()).status, "NEW")

    async def test_ambiguous_initial_cannot_be_resolved_by_model_confidence(self):
        self.event.participants.append({"name": "Илья Петров", "address": "ilya@example.test"})
        self.event.body = "Подготовить договор — отв. Петров И."
        self.assertEqual(
            (await self.create(assignee_address="ivan@example.test")).status, "NEEDS_CONFIRMATION"
        )
