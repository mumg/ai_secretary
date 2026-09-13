from datetime import UTC, datetime
from unittest import TestCase

from improver.models import Meeting, MeetingResult
from improver.services.meeting_results import (
    closest_completed_meeting,
    merge_unique,
    normalize_meeting_title,
    result_brief_summary,
)


class MeetingResultTests(TestCase):
    def test_followup_uses_latest_completed_meeting_from_recurring_thread(self) -> None:
        occurred_at = datetime(2026, 9, 12, 12, tzinfo=UTC)
        old = Meeting(
            source_id="mail",
            external_uid="old",
            source_event_id=None,
            title="Old",
            starts_at=datetime(2026, 9, 5, 9, tzinfo=UTC),
            ends_at=datetime(2026, 9, 5, 10, tzinfo=UTC),
            all_day=False,
            last_event_at=datetime(2026, 9, 5, 10, tzinfo=UTC),
        )
        recent = Meeting(
            source_id="mail",
            external_uid="recent",
            source_event_id=None,
            title="Recent",
            starts_at=datetime(2026, 9, 12, 9, tzinfo=UTC),
            ends_at=datetime(2026, 9, 12, 10, tzinfo=UTC),
            all_day=False,
            last_event_at=datetime(2026, 9, 12, 10, tzinfo=UTC),
        )

        self.assertIs(closest_completed_meeting([old, recent], occurred_at), recent)

    def test_followup_does_not_link_to_future_meeting(self) -> None:
        occurred_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
        future = Meeting(
            source_id="mail",
            external_uid="future",
            source_event_id=None,
            title="Future",
            starts_at=datetime(2026, 9, 3, 9, tzinfo=UTC),
            ends_at=datetime(2026, 9, 3, 10, tzinfo=UTC),
            all_day=False,
            last_event_at=datetime(2026, 9, 3, 10, tzinfo=UTC),
        )

        self.assertIsNone(closest_completed_meeting([future], occurred_at))

    def test_followup_does_not_link_to_meeting_that_is_still_running(self) -> None:
        occurred_at = datetime(2026, 9, 12, 9, 30, tzinfo=UTC)
        running = Meeting(
            source_id="mail",
            external_uid="running",
            source_event_id=None,
            title="Running",
            starts_at=datetime(2026, 9, 12, 9, tzinfo=UTC),
            ends_at=datetime(2026, 9, 12, 10, tzinfo=UTC),
            all_day=False,
            last_event_at=datetime(2026, 9, 12, 9, tzinfo=UTC),
        )

        self.assertIsNone(closest_completed_meeting([running], occurred_at))

    def test_result_prefixes_do_not_change_meeting_identity(self) -> None:
        self.assertEqual(
            normalize_meeting_title("Re: Итоги встречи — Запуск проекта"),
            "запуск проекта",
        )
        self.assertEqual(
            normalize_meeting_title("Протокол: Запуск проекта"),
            "запуск проекта",
        )

    def test_merged_agreements_are_deduplicated(self) -> None:
        self.assertEqual(
            merge_unique(["Отправить смету"], [" отправить   смету ", "Назначить срок"]),
            ["Отправить смету", "Назначить срок"],
        )

    def test_card_brief_prefers_agreements_from_all_sources(self) -> None:
        now = datetime(2026, 9, 12, tzinfo=UTC)
        root = MeetingResult(
            source_id="mts",
            source_event_id=None,
            title="Встреча",
            starts_at=now,
            ends_at=now,
            transcript_status="ready",
            agreements=["Подготовить план"],
            decisions=[],
            summary="Общее резюме",
        )
        mail = MeetingResult(
            source_id="mail",
            source_event_id=None,
            title="Встреча",
            starts_at=now,
            ends_at=now,
            transcript_status="email",
            agreements=["Согласовать бюджет"],
            decisions=[],
        )

        self.assertEqual(
            result_brief_summary(root, [mail]),
            "Подготовить план • Согласовать бюджет",
        )
