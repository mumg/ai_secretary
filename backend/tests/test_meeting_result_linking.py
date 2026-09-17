"""Synthetic regression cases; integration tests use a disposable PostgreSQL only."""

import os
import uuid
from datetime import UTC, datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.models import (
    CommunicationEvent,
    CommunicationSource,
    Meeting,
    MeetingResult,
)
from improver.services.llm import MeetingResultSignal, MeetingTopicMatch, SemanticAnalysis
from improver.services.meeting_results import (
    MTS_TRANSCRIPT_ORIGIN,
    attach_email_results_to_transcript,
    can_attach_result,
    link_result_to_calendar,
    link_results_to_calendar_meeting,
    record_email_meeting_result,
    select_transcript_calendar,
)
from improver.services.mts_link import mts_link_reference_keys


class LinkingRulesTests(TestCase):
    def test_same_title_occurrence_can_overrun_calendar_end(self):
        start = datetime(2026, 9, 15, 13, tzinfo=UTC)
        current = Meeting(title="Weekly sync", status="CONFIRMED", starts_at=start,
                          ends_at=start + timedelta(minutes=30), mts_link_keys=["id:700001"])
        previous = Meeting(title="Weekly sync", status="CONFIRMED", starts_at=start-timedelta(days=14),
                           ends_at=start-timedelta(days=14)+timedelta(minutes=30), mts_link_keys=["id:700001"])
        result = MeetingResult(title="Weekly sync", starts_at=start+timedelta(seconds=29),
                               ends_at=start+timedelta(minutes=58), mts_link_keys=["id:700001"])
        matches = select_transcript_calendar(result, [(previous, CommunicationEvent()), (current, CommunicationEvent())])
        self.assertEqual([item[0] for item in matches], [current])

    def test_shared_calendar_requires_matching_mts_id(self):
        now = datetime.now(UTC)
        calendar_id = uuid.uuid4()
        root = MeetingResult(title="Orion", calendar_meeting_id=calendar_id, starts_at=now)
        self.assertFalse(can_attach_result(root, calendar_id, [], "Pegasus", now))

    def test_same_room_does_not_attach_old_or_future_occurrence(self):
        now = datetime.now(UTC)
        for start in (now - timedelta(days=30), now + timedelta(days=1)):
            root = MeetingResult(title="Orion", starts_at=start, mts_link_keys=["id:700001"])
            self.assertFalse(can_attach_result(root, None, ["id:700001"], "Orion", now))

    def test_transcript_does_not_link_to_previous_week_in_same_room(self):
        now = datetime.now(UTC)
        result = MeetingResult(
            title="Orion",
            starts_at=now,
            ends_at=now + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        meeting = Meeting(
            title="Orion",
            starts_at=now - timedelta(days=7),
            ends_at=now - timedelta(days=7) + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        self.assertEqual(select_transcript_calendar(result, [(meeting, CommunicationEvent())]), [])

    def test_shared_calendar_and_id_allow_different_titles(self):
        now = datetime.now(UTC)
        calendar_id = uuid.uuid4()
        root = MeetingResult(
            title="Room",
            calendar_meeting_id=calendar_id,
            starts_at=now,
            mts_link_keys=["id:700001"],
        )
        self.assertTrue(can_attach_result(root, calendar_id, ["id:700001"], "Orion", now))
        self.assertFalse(can_attach_result(root, uuid.uuid4(), ["id:700001"], "Room", now))

    def test_reused_id_selects_occurrence_by_start_time_and_not_title(self):
        start = datetime(2026, 9, 14, 9, tzinfo=UTC)
        first = Meeting(
            title="Orion",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        second = Meeting(
            title="Pegasus",
            starts_at=start + timedelta(hours=1),
            ends_at=start + timedelta(hours=2),
            mts_link_keys=["id:700001"],
        )
        week_ago = Meeting(
            title="Orion",
            starts_at=start - timedelta(days=7),
            ends_at=start - timedelta(days=7) + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        candidates = [(m, CommunicationEvent()) for m in (first, second, week_ago)]
        for minutes, duration, expected in [
            (0, 60, first),
            (-12, 60, first),  # exactly 80% of the MTS interval is in Outlook
            (-13, 60, None),
            (60, 60, second),
            (72, 60, second),
            (120, 60, None),
        ]:
            with self.subTest(minutes=minutes):
                result = MeetingResult(
                    title="Unrelated room name",
                    starts_at=start + timedelta(minutes=minutes),
                    ends_at=start + timedelta(minutes=minutes + duration),
                    mts_link_keys=["id:700001"],
                )
                matches = select_transcript_calendar(result, candidates)
                self.assertIs(matches[0][0] if matches else None, expected)

    def test_overlapping_outlook_windows_are_ambiguous_even_when_title_matches(self):
        start = datetime.now(UTC)
        first = Meeting(
            title="Orion",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        second = Meeting(
            title="Pegasus",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        result = MeetingResult(
            title="Orion", starts_at=start, ends_at=start, mts_link_keys=["id:700001"]
        )
        self.assertEqual(
            len(
                select_transcript_calendar(
                    result, [(first, CommunicationEvent()), (second, CommunicationEvent())]
                )
            ),
            2,
        )

    def test_timezone_and_different_id(self):
        start = datetime(2026, 9, 14, 9, tzinfo=UTC)
        meeting = Meeting(
            title="Orion",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            mts_link_keys=["id:700001"],
        )
        result = MeetingResult(
            title="Room",
            starts_at=datetime(2026, 9, 14, 12, 15, tzinfo=timezone(timedelta(hours=3))),
            ends_at=datetime(2026, 9, 14, 12, 45, tzinfo=timezone(timedelta(hours=3))),
            mts_link_keys=["id:700001"],
        )
        self.assertIs(
            select_transcript_calendar(result, [(meeting, CommunicationEvent())])[0][0], meeting
        )
        result.mts_link_keys = ["id:700002"]
        self.assertEqual(select_transcript_calendar(result, [(meeting, CommunicationEvent())]), [])


@skipUnless(os.getenv("MEETING_CONTEXT_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class LinkingIntegrationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime.now(UTC)
        self.engine = create_async_engine(os.environ["MEETING_CONTEXT_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.session = async_sessionmaker(
            self.connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )()
        self.session.add(
            CommunicationSource(id="link-synthetic", label="Synthetic", source_type="imap")
        )
        self.analyzer = AsyncMock()
        self.analyzer.match_meeting_topic.return_value = MeetingTopicMatch(
            matches=True, confidence=0.95, evidence="Synthetic topic match"
        )
        await self.session.flush()

    @staticmethod
    def analysis():
        return SemanticAnalysis(
            summary="Synthetic discussion",
            thread_summary="Synthetic discussion",
            keywords=["synthetic"],
        )

    async def asyncTearDown(self):
        await self.session.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def event(self, title, url, occurred_at=None):
        event = CommunicationEvent(
            source_id="link-synthetic",
            source_type="imap",
            external_id=uuid.uuid4().hex,
            content_hash=uuid.uuid4().hex,
            subject=title,
            body=url,
            source_url=url,
            occurred_at=occurred_at or self.now,
            direction="INCOMING",
            event_type="email",
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def calendar(self, title, url):
        event = await self.event(title, url)
        meeting = Meeting(
            source_id="link-synthetic",
            external_uid=uuid.uuid4().hex,
            source_event_id=event.id,
            title=title,
            starts_at=self.now - timedelta(hours=2),
            ends_at=self.now - timedelta(hours=1),
            last_event_at=self.now,
            mts_link_keys=mts_link_reference_keys(url),
            location=url,
        )
        self.session.add(meeting)
        await self.session.flush()
        return meeting, event

    async def transcript(self, title, url):
        event = await self.event(title, url)
        event.semantic_summary = "Synthetic discussion"
        event.semantic_categories = ["Synthetic"]
        event.semantic_keywords = ["synthetic"]
        result = MeetingResult(
            source_id="link-synthetic",
            source_event_id=event.id,
            title=title,
            starts_at=self.now - timedelta(hours=2),
            ends_at=self.now - timedelta(hours=1),
            mts_link_keys=mts_link_reference_keys(url),
            meeting_url=url,
            origin_type=MTS_TRANSCRIPT_ORIGIN,
            transcript_status="ready",
            summary="Synthetic transcript",
        )
        self.session.add(result)
        await self.session.flush()
        return result

    async def test_eighty_percent_overlap_requires_semantic_match(self):
        url = "https://my.mts-link.ru/j/700001"
        calendar, _ = await self.calendar("Calendar topic", url)
        transcript = await self.transcript("Permanent room", url)
        transcript.starts_at = calendar.starts_at - timedelta(minutes=12)
        transcript.ends_at = calendar.ends_at - timedelta(minutes=12)

        self.analyzer.match_meeting_topic.return_value = MeetingTopicMatch(
            matches=False, confidence=0.96, evidence="Synthetic mismatch"
        )
        await link_result_to_calendar(
            self.session, transcript, analysis=self.analysis(), analyzer=self.analyzer
        )
        self.assertIsNone(transcript.calendar_meeting_id)

        self.analyzer.match_meeting_topic.return_value = MeetingTopicMatch(
            matches=True, confidence=0.96, evidence="Synthetic match"
        )
        await link_result_to_calendar(
            self.session, transcript, analysis=self.analysis(), analyzer=self.analyzer
        )
        self.assertEqual(transcript.calendar_meeting_id, calendar.id)
        event = await self.session.get(CommunicationEvent, transcript.source_event_id)
        decision = event.analysis_result["calendar_topic_match"]["candidates"][0]
        self.assertEqual(decision["overlap_ratio"], 0.8)
        self.assertTrue(decision["matches"])

    async def email_result(self, title, url):
        event = await self.event(title, url)
        return await record_email_meeting_result(
            self.session,
            event,
            SemanticAnalysis(summary="Synthetic follow-up", thread_summary="Synthetic follow-up"),
            MeetingResultSignal(detected=True, confidence=1, meeting_title=title),
            self.now,
        )

    async def test_followup_and_transcript_group_in_both_import_orders(self):
        for email_first in (False, True):
            title = "Orion " + str(email_first)
            url = f"https://my.mts-link.ru/j/{700001 + int(email_first)}"
            calendar, _ = await self.calendar(title, url)
            if email_first:
                email = await self.email_result(title, url)
            transcript = await self.transcript(title, url)
            await link_result_to_calendar(
                self.session,
                transcript,
                analysis=self.analysis(),
                analyzer=self.analyzer,
            )
            if email_first:
                await attach_email_results_to_transcript(self.session, transcript)
            else:
                email = await self.email_result(title, url)
            self.assertEqual(transcript.calendar_meeting_id, calendar.id)
            self.assertEqual(email.parent_result_id, transcript.id)

    async def test_common_owner_does_not_merge_different_topics(self):
        first = "https://my.mts-link.ru/j/123456/700001"
        second = "https://my.mts-link.ru/j/123456/700002"
        calendar, _ = await self.calendar("Orion", first)
        await self.calendar("Pegasus", second)
        transcript = await self.transcript("Orion", first)
        await link_result_to_calendar(
            self.session,
            transcript,
            analysis=self.analysis(),
            analyzer=self.analyzer,
        )
        email = await self.email_result("Pegasus", second)
        self.assertEqual(transcript.calendar_meeting_id, calendar.id)
        self.assertNotEqual(email.calendar_meeting_id, calendar.id)
        self.assertIsNone(email.parent_result_id)

    async def test_late_calendar_import_uses_same_rules(self):
        url = "https://my.mts-link.ru/j/700001"
        transcript = await self.transcript("Orion", url)
        email = await self.email_result("Orion", url)
        unrelated = await self.email_result("Pegasus", url)
        calendar, event = await self.calendar("Orion", url)
        await link_results_to_calendar_meeting(self.session, calendar, event, self.analyzer)
        self.assertEqual(transcript.calendar_meeting_id, calendar.id)
        self.assertEqual(email.parent_result_id, transcript.id)
        self.assertIsNone(unrelated.calendar_meeting_id)
        self.assertIsNone(unrelated.parent_result_id)

    async def test_calendar_refresh_preserves_email_group_without_transcript(self):
        url = "https://my.mts-link.ru/j/700001"
        calendar, event = await self.calendar("Orion", url)
        first = await self.email_result("Orion", url)
        second = await self.email_result("Orion", url)
        self.assertEqual(second.parent_result_id, first.id)
        # Distinguish import order for equal timestamps in this synthetic fixture.
        source = await self.session.get(CommunicationEvent, second.source_event_id)
        source.occurred_at += timedelta(seconds=1)
        await link_results_to_calendar_meeting(self.session, calendar, event, self.analyzer)
        self.assertIsNone(first.parent_result_id)
        self.assertEqual(second.parent_result_id, first.id)

    async def test_late_transcript_does_not_hide_nested_email_supplements(self):
        url = "https://my.mts-link.ru/j/700001"
        await self.calendar("Orion", url)
        first = await self.email_result("Orion", url)
        second = await self.email_result("Orion", url)
        transcript = await self.transcript("Orion", url)
        await link_result_to_calendar(
            self.session, transcript, analysis=self.analysis(), analyzer=self.analyzer
        )
        await attach_email_results_to_transcript(self.session, transcript)
        self.assertEqual(first.parent_result_id, transcript.id)
        self.assertEqual(second.parent_result_id, transcript.id)

    async def test_late_calendar_with_different_title_restores_transcript_link(self):
        url = "https://my.mts-link.ru/j/700001"
        transcript = await self.transcript("Permanent room", url)
        calendar, event = await self.calendar("Orion", url)
        await link_results_to_calendar_meeting(self.session, calendar, event, self.analyzer)
        self.assertEqual(transcript.calendar_meeting_id, calendar.id)
        email = await self.email_result("Orion", url)
        self.assertEqual(email.parent_result_id, transcript.id)

    async def test_delayed_email_does_not_choose_latest_reused_room_occurrence(self):
        url = "https://my.mts-link.ru/j/700001"
        old, _ = await self.calendar("Orion", url)
        old.starts_at -= timedelta(days=7)
        old.ends_at -= timedelta(days=7)
        await self.calendar("Orion", url)
        email = await self.email_result("Orion", url)
        self.assertIsNone(email.calendar_meeting_id)
        self.assertIsNone(email.parent_result_id)
