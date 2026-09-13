from types import SimpleNamespace
from unittest import TestCase

from improver.config import AnalysisFilterConfig
from improver.services.analysis_filters import match_analysis_filter


def event(**values: object) -> SimpleNamespace:
    defaults = {
        "subject": "Обычное письмо",
        "body": "Содержимое",
        "author": "sender@example.test",
        "participants": [],
        "raw_headers": {},
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class AnalysisFilterTests(TestCase):
    def test_stop_word_matches_subject_and_body_case_insensitively(self) -> None:
        message = event(
            subject="АВТОМАТИЧЕСКОЕ уведомление",
        )

        match = match_analysis_filter(
            message,
            AnalysisFilterConfig(stop_words=["автоматическое уведомление"]),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "stop_word")
        self.assertEqual(match.value, "автоматическое уведомление")

    def test_excluded_address_matches_sender_or_participant_exactly(self) -> None:
        message = event(
            author="Sender <sender@example.test>",
            participants=[{"name": "Robot", "address": "NoReply@Example.test"}],
        )
        filters = AnalysisFilterConfig(excluded_addresses=["noreply@example.test"])

        match = match_analysis_filter(message, filters)

        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "address")
        self.assertEqual(match.value, "noreply@example.test")

    def test_address_does_not_match_as_substring(self) -> None:
        message = event(
            author="other-noreply@example.test",
        )

        match = match_analysis_filter(
            message,
            AnalysisFilterConfig(excluded_addresses=["noreply@example.test"]),
        )

        self.assertIsNone(match)

    def test_malformed_participant_name_does_not_break_address_matching(self) -> None:
        message = event(
            author="sender@example.test",
            participants=[
                {
                    "name": "Service (automated",
                    "address": "sender@example.test",
                }
            ],
        )

        match = match_analysis_filter(
            message,
            AnalysisFilterConfig(excluded_addresses=["sender@example.test"]),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "address")

    def test_meeting_responses_are_system_filters_in_russian_and_english(self) -> None:
        for subject in ("Принято: Еженедельная встреча", "Declined: Weekly meeting"):
            with self.subTest(subject=subject):
                match = match_analysis_filter(event(subject=subject), AnalysisFilterConfig())

                self.assertIsNotNone(match)
                self.assertEqual(match.kind, "meeting_response")
                self.assertTrue(match.technical)

    def test_calendar_reply_mime_marker_is_filtered_without_subject_prefix(self) -> None:
        match = match_analysis_filter(
            event(raw_headers={"Calendar-Method": "REPLY"}),
            AnalysisFilterConfig(),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "meeting_response")

    def test_calendar_reply_with_explicit_results_reaches_semantic_analysis(self) -> None:
        match = match_analysis_filter(
            event(
                subject="Принято: Проектная встреча",
                body="По итогам встречи договорились подготовить план внедрения.",
                raw_headers={"Calendar-Method": "REPLY"},
            ),
            AnalysisFilterConfig(),
        )

        self.assertIsNone(match)

    def test_automatic_absence_reply_is_detected_by_header_or_subject(self) -> None:
        messages = (
            event(raw_headers={"Auto-Submitted": "auto-replied"}),
            event(subject="Автоматический ответ: отпуск"),
            event(subject="Out of Office: vacation"),
        )
        for message in messages:
            with self.subTest(message=message):
                match = match_analysis_filter(message, AnalysisFilterConfig())

                self.assertIsNotNone(match)
                self.assertEqual(match.kind, "automatic_reply")
                self.assertTrue(match.technical)
