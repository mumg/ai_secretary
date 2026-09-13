from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import holidays

from improver.config import AppConfig


class BusinessCalendar:
    def __init__(self, config: AppConfig):
        self.config = config
        self.timezone = ZoneInfo(config.server.timezone)
        self.workday_start = time.fromisoformat(config.calendar.workday_start)
        self.workday_end = time.fromisoformat(config.calendar.workday_end)
        self.working_dates = {date.fromisoformat(value) for value in config.calendar.working_dates}
        self.non_working_dates = {
            date.fromisoformat(value) for value in config.calendar.non_working_dates
        }
        self._holidays: dict[int, holidays.HolidayBase] = {}

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def _country_holidays(self, year: int) -> holidays.HolidayBase:
        if year not in self._holidays:
            self._holidays[year] = holidays.country_holidays(
                self.config.calendar.country, years=[year]
            )
        return self._holidays[year]

    def is_working_day(self, value: date) -> bool:
        if value in self.working_dates:
            return True
        if value in self.non_working_dates:
            return False
        if value.weekday() >= 5:
            return False
        return value not in self._country_holidays(value.year)

    def previous_working_day(self, value: date) -> date:
        current = value
        while not self.is_working_day(current):
            current -= timedelta(days=1)
        return current

    def next_working_day(self, value: date) -> date:
        current = value
        while not self.is_working_day(current):
            current += timedelta(days=1)
        return current

    def normalize_due(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        local = (
            value.astimezone(self.timezone) if value.tzinfo else value.replace(tzinfo=self.timezone)
        )
        normalized_date = self.previous_working_day(local.date())
        if normalized_date == local.date():
            return local
        return datetime.combine(normalized_date, local.time(), tzinfo=self.timezone)

    def end_of_workday(self, value: date) -> datetime:
        return datetime.combine(value, self.workday_end, tzinfo=self.timezone)
