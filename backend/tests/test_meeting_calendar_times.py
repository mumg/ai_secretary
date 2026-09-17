from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from improver.models import CommunicationEvent, Meeting
from improver.services.llm import MeetingResultSignal, SemanticAnalysis
from improver.services.meeting_results import _find_calendar_for_email, record_email_meeting_result
from improver.services.mts_link import mts_link_reference_keys, references_overlap


class SessionLinkTests(TestCase):
    def test_explicit_session_route_uses_session_not_owner_or_event(self):
        keys = mts_link_reference_keys("https://mts.mts-link.ru/j/MTC/700001/session/800002")
        self.assertIn("id:800002", keys)
        self.assertNotIn("id:700001", keys)
        self.assertNotIn("id:mtc", keys)
        self.assertTrue(references_overlap(keys, mts_link_reference_keys(known_ids=[800002])))
        other = mts_link_reference_keys("https://mts.mts-link.ru/j/MTC/700001/session/800003")
        self.assertFalse(references_overlap(keys, other))

    def test_incomplete_or_unrelated_routes_are_not_sessions(self):
        for path in (
            "/j/MTC/700001/session", "/j/MTC/700001/session/settings",
            "/j/MTC/700001/record/800002", "/j/MTC/700001/session/800002/settings",
        ):
            self.assertEqual(mts_link_reference_keys("https://mts.mts-link.ru" + path), [])


class EmailCalendarTimeTests(IsolatedAsyncioTestCase):
    async def test_original_subject_matches_when_extracted_title_is_paraphrased(self):
        url = "https://mts.mts-link.ru/j/MTC/700001/session/800002"
        meeting = Meeting(
            id=uuid4(), title="Рекомендации по обучению", status="CONFIRMED", location=url,
            starts_at=datetime(2026, 9, 14, 13, 30, tzinfo=UTC),
            ends_at=datetime(2026, 9, 14, 14, tzinfo=UTC), mts_link_keys=[],
        )
        event = CommunicationEvent(
            subject="RE: Рекомендации по обучению",
            occurred_at=datetime(2026, 9, 14, 15, tzinfo=UTC),
        )
        signal = MeetingResultSignal(
            detected=True, confidence=1, meeting_title="Обсуждение рекомендаций по обучению",
        )
        candidates = [(meeting, CommunicationEvent(body=url))]
        result = await _find_calendar_for_email(
            None, event, signal, mts_link_reference_keys(url), candidates=candidates,
        )
        self.assertIs(result, meeting)
        result = await _find_calendar_for_email(
            None, event, signal, ["id:different-session"], candidates=candidates,
        )
        self.assertIsNone(result)

    async def test_subject_grouping_preserves_calendar_provider_thread(self):
        url = "https://mts.mts-link.ru/j/MTC/700001/session/800002"
        email = CommunicationEvent(
            source_id="mail", subject="Обсуждение CJ", thread_external_id="subject:merged",
            raw_headers={"Original-Thread-Id": "exchange-specific-occurrence"},
            occurred_at=datetime(2026, 9, 14, 16, tzinfo=UTC),
        )
        candidates = [(Meeting(
            id=uuid4(), title="Обсуждение CJ", status="CONFIRMED", location=url,
            starts_at=datetime(2026, 9, day, 14, tzinfo=UTC),
            ends_at=datetime(2026, 9, day, 15, tzinfo=UTC), mts_link_keys=[],
        ), CommunicationEvent(
            source_id="mail", body=url,
            thread_external_id="exchange-specific-occurrence" if day == 14 else "another",
        )) for day in (7, 14)]
        result = await _find_calendar_for_email(
            None, email, MeetingResultSignal(detected=True, confidence=1),
            mts_link_reference_keys(url), candidates=candidates,
        )
        self.assertIs(result, candidates[1][0])

    async def test_email_inherits_calendar_interval_without_transcript(self):
        url = "https://mts.mts-link.ru/j/MTC/700001/session/800002"
        meeting = Meeting(
            id=uuid4(), title="Обсуждение CJ", status="CONFIRMED", location=url,
            starts_at=datetime(2026, 9, 14, 14, tzinfo=UTC),
            ends_at=datetime(2026, 9, 14, 15, tzinfo=UTC), mts_link_keys=[],
        )
        event = CommunicationEvent(
            id=uuid4(), source_id="mail", subject="RE: Обсуждение CJ",
            body=f"Итоги обсуждения. {url}",
            occurred_at=datetime(2026, 9, 14, 15, 37, 37, tzinfo=UTC),
            content_hash="synthetic-hash", direction="INCOMING",
        )
        session = Mock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
        with (
            patch("improver.services.meeting_results._calendar_candidates", new=AsyncMock(
                return_value=[(meeting, CommunicationEvent(body=url))],
            )),
            patch("improver.services.meeting_results._find_existing_root", new=AsyncMock(
                return_value=None,
            )),
        ):
            result = await record_email_meeting_result(
                session, event, SemanticAnalysis(
                    summary="Обсудили вопросы", thread_summary="Обсудили вопросы",
                ),
                MeetingResultSignal(detected=True, confidence=1, meeting_title="Обсуждение CJ"),
                event.occurred_at,
            )
        self.assertEqual(result.calendar_meeting_id, meeting.id)
        self.assertEqual(result.starts_at, meeting.starts_at)
        self.assertEqual(result.ends_at, meeting.ends_at)
        self.assertNotEqual(result.starts_at, event.occurred_at)
        self.assertIsNone(result.transcript_id)

    async def test_reused_session_and_title_still_require_unambiguous_calendar(self):
        url = "https://mts.mts-link.ru/j/MTC/700001/session/800002"
        event = CommunicationEvent(
            subject="Обсуждение CJ", occurred_at=datetime(2026, 9, 14, 16, tzinfo=UTC),
        )
        candidates = [(Meeting(
            id=uuid4(), title="Обсуждение CJ", status="CONFIRMED", location=url,
            starts_at=datetime(2026, 9, day, 14, tzinfo=UTC),
            ends_at=datetime(2026, 9, day, 15, tzinfo=UTC), mts_link_keys=[],
        ), CommunicationEvent(body=url)) for day in (7, 14)]
        result = await _find_calendar_for_email(
            None, event, MeetingResultSignal(detected=True, confidence=1),
            mts_link_reference_keys(url), candidates=candidates,
        )
        self.assertIsNone(result)
