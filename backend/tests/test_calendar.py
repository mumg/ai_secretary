from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from improver.config import AppConfig
from improver.services.calendar import BusinessCalendar


class BusinessCalendarTests(TestCase):
    def setUp(self) -> None:
        self.calendar = BusinessCalendar(AppConfig())
        self.tz = ZoneInfo("Europe/Moscow")

    def test_weekend_deadline_moves_to_previous_workday(self) -> None:
        saturday = datetime(2026, 9, 12, 15, 0, tzinfo=self.tz)
        normalized = self.calendar.normalize_due(saturday)
        self.assertEqual(normalized, datetime(2026, 9, 11, 15, 0, tzinfo=self.tz))

    def test_manual_non_working_date_is_honored(self) -> None:
        config = AppConfig.model_validate({"calendar": {"non_working_dates": ["2026-09-11"]}})
        calendar = BusinessCalendar(config)
        friday = datetime(2026, 9, 11, 16, 30, tzinfo=self.tz)
        self.assertEqual(
            calendar.normalize_due(friday),
            datetime(2026, 9, 10, 16, 30, tzinfo=self.tz),
        )
