"""Synthetic meeting preparation tests. PostgreSQL tests require a disposable migrated DB.

MEETING_CONTEXT_TEST_DATABASE_URL must never point at the working archive.
Every test uses an outer rollback transaction, including worker/API commits.
"""

import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.config import AppConfig
from improver.models import (
    CommunicationEvent,
    CommunicationSource,
    DailyPlan,
    Meeting,
    MeetingContext,
    MeetingResult,
)
from improver.services.llm import GroundedChatAnswer
from improver.services.meeting_context import (
    collect_materials,
    ensure_context,
    next_refresh,
    notify_next_meeting_context,
    prepare_next_meeting_context,
    topic_terms,
)


@asynccontextmanager
async def no_llm_lock():
    yield


class MeetingContextRulesTests(TestCase):
    def test_generic_titles_do_not_search_random_archive_messages(self):
        self.assertEqual(topic_terms("Еженедельная встреча"), [])
        self.assertIn("orion", topic_terms("Еженедельная встреча Orion"))

    def test_refresh_is_more_frequent_near_start(self):
        now = datetime.now(UTC)
        self.assertEqual(
            next_refresh(Meeting(starts_at=now + timedelta(hours=2)), now),
            now + timedelta(minutes=15),
        )
        self.assertEqual(
            next_refresh(Meeting(starts_at=now + timedelta(days=7)), now), now + timedelta(hours=1)
        )


@skipUnless(os.getenv("MEETING_CONTEXT_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class MeetingContextIntegrationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime.now(UTC)
        self.config = AppConfig()
        self.config.server.timezone = "UTC"
        self.engine = create_async_engine(os.environ["MEETING_CONTEXT_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.factory = async_sessionmaker(
            self.connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        await self.insert(
            CommunicationSource(id="context-synthetic", label="Synthetic", source_type="imap"),
            DailyPlan(plan_date=self.now.date(), generated_at=self.now),
        )
        self.analyzer = AsyncMock()
        self.analyzer.select_relevant_references.return_value = ["E1"]
        self.analyzer.answer_from_archive.return_value = GroundedChatAnswer(
            answer="Согласовали бюджет [E1]. Открытый вопрос: дата запуска.",
            used_reference_ids=["E1"],
        )
        for target, replacement in (
            ("improver.services.meeting_context.OllamaAnalyzer", lambda _: self.analyzer),
            ("improver.services.meeting_context.ollama_request_slot", no_llm_lock),
        ):
            item = patch(target, replacement)
            item.start()
            self.addCleanup(item.stop)

    async def asyncTearDown(self):
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def insert(self, *rows):
        async with self.factory() as session:
            session.add_all(rows)
            await session.commit()

    async def event(self, **kwargs):
        values = dict(
            id=uuid.uuid4(),
            source_id="context-synthetic",
            source_type="imap",
            external_id=uuid.uuid4().hex,
            content_hash=uuid.uuid4().hex,
            event_type="email",
            occurred_at=self.now - timedelta(days=1),
            subject="Orion",
            body="Orion: согласовали бюджет",
            analysis_state="COMPLETED",
        )
        values.update(kwargs)
        event = CommunicationEvent(**values)
        await self.insert(event)
        return event

    async def meeting(self, **kwargs):
        invitation = await self.event(event_type="meeting_invitation", thread_external_id="invite")
        values = dict(
            id=uuid.uuid4(),
            source_id="context-synthetic",
            external_uid=uuid.uuid4().hex,
            source_event_id=invitation.id,
            title="Orion",
            starts_at=self.now,
            ends_at=self.now + timedelta(hours=1),
            last_event_at=self.now,
        )
        values.update(kwargs)
        meeting = Meeting(**values)
        await self.insert(meeting)
        return meeting

    async def result(self, **kwargs):
        event = await self.event(
            event_type="meeting_transcript", occurred_at=self.now - timedelta(days=3)
        )
        values = dict(
            source_id="context-synthetic",
            source_event_id=event.id,
            title="Orion",
            starts_at=self.now - timedelta(days=3),
            ends_at=self.now - timedelta(days=3, hours=-1),
            transcript_status="COMPLETED",
            summary="Обсудили запуск",
            decisions=["Согласовали бюджет"],
            agreements=["Уточнить сроки"],
        )
        values.update(kwargs)
        result = MeetingResult(**values)
        await self.insert(result)
        return result

    async def context(self, meeting):
        async with self.factory() as session:
            return await session.get(MeetingContext, meeting.id)

    async def make_due(self, meeting, **kwargs):
        async with self.factory() as session:
            row = await session.get(MeetingContext, meeting.id)
            row.next_refresh_at = self.now - timedelta(seconds=1)
            for key, value in kwargs.items():
                setattr(row, key, value)
            await session.commit()

    async def run_worker(self):
        return await prepare_next_meeting_context(self.config, self.factory)

    async def test_materials_keep_previous_decisions_and_links_but_exclude_future(self):
        meeting = await self.meeting()
        previous = await self.result()
        mail = await self.event()
        await self.event(occurred_at=self.now + timedelta(hours=1), body="Orion future decision")
        await self.result(title="Orion future", ends_at=self.now + timedelta(hours=1))
        await self.event(analysis_state="IGNORED")
        async with self.factory() as session:
            refs, cards = await collect_materials(session, self.config, meeting, self.now)
        self.assertEqual({ref.id for ref in refs}, {previous.source_event_id, mail.id})
        self.assertEqual(refs[0].meeting_result_id, previous.id)
        self.assertIn("Согласовали бюджет", cards[0]["summary"])
        self.assertIn("Уточнить сроки", cards[0]["summary"])

    async def test_generic_title_uses_linked_thread_without_unrelated_or_mailing_messages(self):
        meeting = await self.meeting(title="Встреча")
        linked = await self.event(subject="Без темы", thread_external_id="invite")
        await self.event(subject="Без темы", thread_external_id="other")
        await self.event(thread_external_id="invite", is_mailing=True)
        await self.result(title="Встреча")
        async with self.factory() as session:
            refs, _ = await collect_materials(session, self.config, meeting, self.now)
        self.assertEqual([ref.id for ref in refs], [linked.id])

    async def test_title_with_relative_date_is_literal_search_data(self):
        meeting = await self.meeting(title="Orion планы на завтра")
        mail = await self.event()
        async with self.factory() as session:
            refs, _ = await collect_materials(session, self.config, meeting, self.now)
        self.assertIn(mail.id, [ref.id for ref in refs])

    async def test_invitations_and_mailings_cannot_crowd_out_older_correspondence(self):
        meeting = await self.meeting()
        mail = await self.event(
            occurred_at=self.now - timedelta(days=90), thread_external_id="decision"
        )
        for _ in range(17):
            await self.event(event_type="meeting_invitation", thread_external_id="decision")
            await self.event(is_mailing=True, thread_external_id="decision")
        async with self.factory() as session:
            refs, _ = await collect_materials(session, self.config, meeting, self.now)
        self.assertEqual([ref.id for ref in refs], [mail.id])

    async def test_shared_meeting_link_finds_result_even_after_rename(self):
        meeting = await self.meeting(title="Встреча", mts_link_keys=["synthetic-session"])
        result = await self.result(title="Старое название", mts_link_keys=["synthetic-session"])
        async with self.factory() as session:
            refs, _ = await collect_materials(session, self.config, meeting, self.now)
        self.assertEqual([ref.meeting_result_id for ref in refs], [result.id])

    async def test_background_prepares_before_opening_and_reuses_unchanged_materials(self):
        meeting = await self.meeting()
        await self.result()
        self.assertTrue(await self.run_worker())
        row = await self.context(meeting)
        self.assertEqual(row.status, "READY")
        self.assertEqual(len(row.references), 1)
        self.assertIsNotNone(row.generated_at)
        self.assertFalse(await self.run_worker())
        await self.make_due(meeting)
        self.assertTrue(await self.run_worker())
        self.analyzer.answer_from_archive.assert_awaited_once()

    async def test_new_evidence_triggers_regeneration(self):
        meeting = await self.meeting()
        await self.result()
        await self.run_worker()
        await self.event(body="Orion: сроки перенесли")
        await self.make_due(meeting)
        await self.run_worker()
        self.assertEqual(self.analyzer.answer_from_archive.await_count, 2)

    async def test_empty_archive_is_explicit_and_does_not_call_qwen(self):
        meeting = await self.meeting(title="Встреча")
        await self.run_worker()
        row = await self.context(meeting)
        self.assertEqual(row.status, "EMPTY")
        self.assertFalse(row.references)
        self.assertIn("не найдены", row.summary)
        self.analyzer.select_relevant_references.assert_not_awaited()

    async def test_nearest_unseen_first_and_cancelled_or_ended_are_skipped(self):
        later = await self.meeting(
            starts_at=self.now + timedelta(days=3), ends_at=self.now + timedelta(days=3, hours=1)
        )
        sooner = await self.meeting()
        await self.meeting(status="CANCELLED")
        await self.meeting(
            starts_at=self.now - timedelta(hours=2), ends_at=self.now - timedelta(hours=1)
        )
        await self.run_worker()
        self.assertIsNotNone(await self.context(sooner))
        self.assertIsNone(await self.context(later))
        self.assertFalse(await self.run_worker())

    async def test_unreferenced_answer_is_not_shown_as_factual_context(self):
        meeting = await self.meeting()
        await self.result()
        self.analyzer.answer_from_archive.return_value = GroundedChatAnswer(
            answer="invented", used_reference_ids=["E999"]
        )
        await self.run_worker()
        row = await self.context(meeting)
        self.assertEqual(row.status, "EMPTY")
        self.assertNotIn("invented", row.summary)
        self.assertFalse(row.references)

    async def test_failed_inference_retries_after_delay(self):
        meeting = await self.meeting()
        await self.result()
        self.analyzer.select_relevant_references.side_effect = TimeoutError("synthetic")
        await self.run_worker()
        self.assertEqual((await self.context(meeting)).status, "FAILED")
        self.assertFalse(await self.run_worker())
        self.analyzer.select_relevant_references.side_effect = None
        await self.make_due(meeting)
        await self.run_worker()
        self.assertEqual((await self.context(meeting)).status, "READY")

    async def test_changed_meeting_discards_inflight_result(self):
        meeting = await self.meeting()
        await self.result()

        async def change_meeting(*args):
            async with self.factory() as session:
                current = await session.get(Meeting, meeting.id)
                current.title = "Новая тема"
                await session.commit()
            return ["E1"]

        self.analyzer.select_relevant_references.side_effect = change_meeting
        await self.run_worker()
        row = await self.context(meeting)
        self.assertEqual(row.status, "NOT_REQUESTED")
        self.assertIsNone(row.summary)

    async def test_stale_processing_lease_is_reclaimed(self):
        meeting = await self.meeting(title="Встреча")
        async with self.factory() as session:
            row = await ensure_context(session, meeting)
            row.status = "PROCESSING"
            row.started_at = self.now - timedelta(days=1)
            await session.commit()
        self.assertTrue(await self.run_worker())
        self.assertEqual((await self.context(meeting)).status, "EMPTY")

    async def test_active_processing_lease_prevents_duplicate_work(self):
        meeting = await self.meeting()
        async with self.factory() as session:
            row = await ensure_context(session, meeting)
            row.status, row.started_at = "PROCESSING", self.now
            await session.commit()
        self.assertFalse(await self.run_worker())
        self.analyzer.select_relevant_references.assert_not_awaited()

    async def test_replaced_generation_cannot_be_overwritten_by_old_worker(self):
        meeting = await self.meeting()
        await self.result()
        replacement = uuid.uuid4()

        async def replace_generation(*args):
            async with self.factory() as session:
                row = await session.get(MeetingContext, meeting.id)
                row.generation = replacement
                await session.commit()
            return ["E1"]

        self.analyzer.select_relevant_references.side_effect = replace_generation
        await self.run_worker()
        row = await self.context(meeting)
        self.assertEqual(row.generation, replacement)
        self.assertEqual(row.status, "PROCESSING")
        self.assertIsNone(row.summary)

    async def test_ended_or_cancelled_meeting_does_not_leave_client_polling_forever(self):
        from improver.api.meetings import context_detail

        for meeting, status in (
            (await self.meeting(status="CANCELLED"), "CANCELLED"),
            (
                await self.meeting(
                    starts_at=self.now - timedelta(hours=2), ends_at=self.now - timedelta(hours=1)
                ),
                "ENDED",
            ),
        ):
            async with self.factory() as session:
                detail = await context_detail(meeting.id, session, refresh=True)
            self.assertEqual(detail.status, status)
        self.assertFalse(await self.run_worker())

    async def test_api_returns_cache_and_refresh_queues_without_inference(self):
        from improver.api.meetings import context_detail

        meeting = await self.meeting()
        await self.result()
        async with self.factory() as session:
            detail = await context_detail(meeting.id, session)
        self.assertEqual(detail.status, "NOT_REQUESTED")
        self.analyzer.select_relevant_references.assert_not_awaited()
        await self.run_worker()
        async with self.factory() as session:
            detail = await context_detail(meeting.id, session)
        self.assertEqual(detail.status, "READY")
        self.assertEqual(detail.meeting.id, meeting.id)
        async with self.factory() as session:
            detail = await context_detail(meeting.id, session, refresh=True)
        self.assertEqual(detail.status, "PENDING")
        self.assertIsNotNone(detail.summary)
        await self.run_worker()
        self.assertEqual(self.analyzer.answer_from_archive.await_count, 2)

    async def test_api_invalidates_after_reschedule_and_returns_404_for_missing_meeting(self):
        from fastapi import HTTPException

        from improver.api.meetings import context_detail

        meeting = await self.meeting()
        await self.result()
        await self.run_worker()
        async with self.factory() as session:
            current = await session.scalar(select(Meeting).where(Meeting.id == meeting.id))
            current.starts_at += timedelta(days=2)
            await session.commit()
        async with self.factory() as session:
            detail = await context_detail(meeting.id, session)
        self.assertEqual(detail.status, "NOT_REQUESTED")
        self.assertIsNotNone(detail.summary)
        self.assertTrue(detail.stale)
        async with self.factory() as session:
            with self.assertRaises(HTTPException) as caught:
                await context_detail(uuid.uuid4(), session)
        self.assertEqual(caught.exception.status_code, 404)

    async def test_future_meeting_is_manual_once_and_can_be_refreshed(self):
        from improver.api.meetings import context_detail

        meeting = await self.meeting(
            starts_at=self.now + timedelta(days=2), ends_at=self.now + timedelta(days=2, hours=1)
        )
        await self.result()
        async with self.factory() as session:
            detail = await context_detail(meeting.id, session)
        self.assertEqual(detail.status, "NOT_REQUESTED")
        self.assertFalse(await self.run_worker())
        async with self.factory() as session:
            detail = await context_detail(meeting.id, session, refresh=True)
        self.assertEqual(detail.status, "PENDING")
        await self.run_worker()
        first = await self.context(meeting)
        self.assertEqual(first.status, "READY")
        self.assertIsNone(first.requested_at)
        self.assertIsNotNone(first.notify_after)
        await self.make_due(meeting)
        self.assertFalse(await self.run_worker())
        async with self.factory() as session:
            cached = await context_detail(meeting.id, session)
        self.assertEqual(cached.summary, first.summary)
        self.assertEqual(cached.status, "READY")
        async with self.factory() as session:
            refreshed = await context_detail(meeting.id, session, refresh=True)
        self.assertEqual(refreshed.summary, first.summary)
        self.assertEqual(refreshed.status, "PENDING")
        await self.run_worker()
        self.assertEqual(self.analyzer.answer_from_archive.await_count, 2)

    async def test_daily_plan_creation_queues_only_today(self):
        from improver.services.plans import rebuild_plan

        async with self.factory() as session:
            await session.execute(delete(DailyPlan))
            await session.commit()
        today = await self.meeting()
        tomorrow = await self.meeting(
            starts_at=self.now + timedelta(days=1), ends_at=self.now + timedelta(days=1, hours=1)
        )
        self.assertFalse(await self.run_worker())
        async with self.factory() as session:
            await rebuild_plan(session, self.now)
            await session.commit()
        self.assertEqual((await self.context(today)).status, "PENDING")
        self.assertIsNone(await self.context(tomorrow))
        self.assertTrue(await self.run_worker())
        self.assertIsNone((await self.context(today)).notify_after)
        self.assertFalse(await self.run_worker())

    async def test_today_uses_server_timezone_and_excludes_next_midnight(self):
        from datetime import time
        from zoneinfo import ZoneInfo

        self.config.server.timezone = "Pacific/Kiritimati"
        local_now = self.now.astimezone(ZoneInfo(self.config.server.timezone))
        end = datetime.combine(
            local_now.date() + timedelta(days=1), time.min, tzinfo=local_now.tzinfo
        )
        async with self.factory() as session:
            await session.execute(delete(DailyPlan))
            session.add(DailyPlan(plan_date=local_now.date(), generated_at=self.now))
            await session.commit()
        inside = await self.meeting(starts_at=end - timedelta(minutes=1), ends_at=end)
        outside = await self.meeting(starts_at=end, ends_at=end + timedelta(hours=1))
        self.assertTrue(await self.run_worker())
        self.assertIsNotNone(await self.context(inside))
        self.assertFalse(await self.run_worker())
        self.assertIsNone(await self.context(outside))

    async def test_automatic_context_never_queues_push(self):
        meeting = await self.meeting()
        await self.result()
        await self.run_worker()
        self.assertIsNone((await self.context(meeting)).notify_after)
        with patch(
            "improver.services.meeting_context.NotificationService.send", new_callable=AsyncMock
        ) as send:
            self.assertFalse(await notify_next_meeting_context(self.config, self.factory))
            send.assert_not_awaited()
        await self.make_due(meeting)
        await self.run_worker()
        self.assertIsNone((await self.context(meeting)).notify_after)

    async def test_manual_refresh_for_today_never_queues_push(self):
        from improver.api.meetings import context_detail

        meeting = await self.meeting(starts_at=self.now, ends_at=self.now + timedelta(hours=1))
        await self.result()
        async with self.factory() as session:
            await context_detail(meeting.id, session, refresh=True)
        await self.run_worker()
        row = await self.context(meeting)
        self.assertEqual(row.status, "READY")
        self.assertIsNone(row.notify_after)

    async def test_manual_push_is_durable_retried_and_sent_once(self):
        meeting = await self.meeting(
            starts_at=self.now + timedelta(days=2),
            ends_at=self.now + timedelta(days=2, hours=1),
        )
        await self.result()
        async with self.factory() as session:
            await ensure_context(session, meeting, force=True)
            await session.commit()
        await self.run_worker()
        self.assertIsNotNone((await self.context(meeting)).notify_after)
        with patch(
            "improver.services.meeting_context.NotificationService.send", new_callable=AsyncMock
        ) as send:
            send.return_value = 0
            self.assertTrue(await notify_next_meeting_context(self.config, self.factory))
            self.assertFalse(await notify_next_meeting_context(self.config, self.factory))
            async with self.factory() as session:
                row = await session.get(MeetingContext, meeting.id)
                row.notify_after = self.now - timedelta(seconds=1)
                await session.commit()
            send.return_value = 1
            self.assertTrue(await notify_next_meeting_context(self.config, self.factory))
            self.assertEqual(send.await_args.args[1:], ("MEETING_CONTEXT_READY", str(meeting.id)))
            self.assertFalse(await notify_next_meeting_context(self.config, self.factory))

    async def test_manual_retry_of_future_meeting_survives_inference_failure(self):
        meeting = await self.meeting(
            starts_at=self.now + timedelta(days=2), ends_at=self.now + timedelta(days=2, hours=1)
        )
        await self.result()
        async with self.factory() as session:
            await ensure_context(session, meeting, force=True)
            await session.commit()
        self.analyzer.select_relevant_references.side_effect = TimeoutError()
        await self.run_worker()
        self.assertIsNotNone((await self.context(meeting)).requested_at)
        self.analyzer.select_relevant_references.side_effect = None
        await self.make_due(meeting)
        await self.run_worker()
        self.assertEqual((await self.context(meeting)).status, "READY")
        self.assertIsNone((await self.context(meeting)).requested_at)

    async def test_push_failure_does_not_discard_ready_context(self):
        meeting = await self.meeting(
            starts_at=self.now + timedelta(days=2),
            ends_at=self.now + timedelta(days=2, hours=1),
        )
        await self.result()
        async with self.factory() as session:
            await ensure_context(session, meeting, force=True)
            await session.commit()
        await self.run_worker()
        with patch(
            "improver.services.meeting_context.NotificationService.send",
            new_callable=AsyncMock,
            side_effect=ValueError("synthetic"),
        ):
            self.assertTrue(await notify_next_meeting_context(self.config, self.factory))
        row = await self.context(meeting)
        self.assertEqual(row.status, "READY")
        self.assertGreater(row.notify_after, self.now)

    async def test_cancelled_meeting_does_not_send_stale_push(self):
        meeting = await self.meeting(
            starts_at=self.now + timedelta(days=2),
            ends_at=self.now + timedelta(days=2, hours=1),
        )
        await self.result()
        async with self.factory() as session:
            await ensure_context(session, meeting, force=True)
            await session.commit()
        await self.run_worker()
        async with self.factory() as session:
            current = await session.get(Meeting, meeting.id)
            current.status = "CANCELLED"
            await session.commit()
        with patch(
            "improver.services.meeting_context.NotificationService.send", new_callable=AsyncMock
        ) as send:
            self.assertTrue(await notify_next_meeting_context(self.config, self.factory))
            send.assert_not_awaited()
        self.assertIsNone((await self.context(meeting)).notify_after)
