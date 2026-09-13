from unittest import TestCase

from improver.services.calendar_events import (
    parse_calendar_event,
    reinterpret_utc_calendar_as_local,
)


class CalendarEventTests(TestCase):
    def test_parses_request_with_timezone_and_participants(self) -> None:
        event = parse_calendar_event(
            "\r\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "METHOD:REQUEST",
                    "BEGIN:VEVENT",
                    "UID:meeting-1@example.test",
                    "DTSTART;TZID=Europe/Moscow:20260911T103000",
                    "DTEND;TZID=Europe/Moscow:20260911T113000",
                    "SUMMARY:Рабочая встреча",
                    "LOCATION:Переговорная 1",
                    'ORGANIZER;CN="Иван":mailto:ivan@example.test',
                    'ATTENDEE;CN="Максим":mailto:maxim@example.test',
                    "END:VEVENT",
                    "END:VCALENDAR",
                ]
            )
        )

        assert event is not None
        self.assertEqual(event["uid"], "meeting-1@example.test")
        self.assertEqual(event["starts_at"], "2026-09-11T10:30:00+03:00")
        self.assertEqual(event["ends_at"], "2026-09-11T11:30:00+03:00")
        self.assertEqual(event["organizer"], {"name": "Иван", "address": "ivan@example.test"})
        self.assertEqual(event["attendees"], [{"name": "Максим", "address": "maxim@example.test"}])

    def test_cancel_method_marks_meeting_cancelled(self) -> None:
        event = parse_calendar_event(
            "BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nUID:meeting-2\n"
            "DTSTART:20260911T120000Z\nSUMMARY:Встреча\nEND:VEVENT\nEND:VCALENDAR"
        )

        assert event is not None
        self.assertEqual(event["status"], "CANCELLED")

    def test_parses_outlook_timezone_with_colon_inside_quotes(self) -> None:
        event = parse_calendar_event(
            "BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nUID:meeting-4\n"
            'DTSTART;TZID="(UTC+03:00) Example Time":20260911T120000\n'
            'DTEND;TZID="(UTC+03:00) Example Time":20260911T130000\n'
            "END:VEVENT\nEND:VCALENDAR"
        )

        assert event is not None
        self.assertEqual(event["starts_at"], "2026-09-11T12:00:00+03:00")

    def test_reply_is_not_a_meeting_invitation(self) -> None:
        event = parse_calendar_event(
            "BEGIN:VCALENDAR\nMETHOD:REPLY\nBEGIN:VEVENT\nUID:meeting-3\n"
            "DTSTART:20260911T120000Z\nEND:VEVENT\nEND:VCALENDAR"
        )

        self.assertIsNone(event)

    def test_reinterprets_exchange_utc_marker_without_shifting_wall_time(self) -> None:
        corrected = reinterpret_utc_calendar_as_local(
            {
                "starts_at": "2026-09-11T12:00:00+00:00",
                "ends_at": "2026-09-11T13:00:00+00:00",
            },
            "Europe/Moscow",
        )

        self.assertEqual(corrected["starts_at"], "2026-09-11T12:00:00+03:00")
        self.assertEqual(corrected["ends_at"], "2026-09-11T13:00:00+03:00")

    def test_does_not_change_already_localized_exchange_time(self) -> None:
        corrected = reinterpret_utc_calendar_as_local(
            {
                "starts_at": "2026-09-11T12:00:00+03:00",
                "ends_at": "2026-09-11T13:00:00+03:00",
            },
            "Europe/Moscow",
        )

        self.assertEqual(corrected["starts_at"], "2026-09-11T12:00:00+03:00")
