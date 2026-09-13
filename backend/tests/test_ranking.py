from datetime import datetime, timedelta
from unittest import TestCase
from zoneinfo import ZoneInfo

from improver.enums import TaskPriority, TaskStatus
from improver.models import Task
from improver.services.ranking import rank_task


class RankingTests(TestCase):
    def test_overdue_task_outranks_future_task_of_same_priority(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        overdue = Task(
            title="Просрочено",
            priority=TaskPriority.NORMAL,
            status=TaskStatus.NEW,
            due_at=now - timedelta(days=1),
        )
        future = Task(
            title="Позже",
            priority=TaskPriority.NORMAL,
            status=TaskStatus.NEW,
            due_at=now + timedelta(days=10),
        )
        overdue_score, reasons = rank_task(overdue, now)
        future_score, _ = rank_task(future, now)
        self.assertGreater(overdue_score, future_score)
        self.assertIn("задача просрочена", reasons)

    def test_critical_priority_has_larger_base_score(self) -> None:
        now = datetime(2026, 9, 10, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        critical = Task(title="Важно", priority=TaskPriority.CRITICAL, status=TaskStatus.NEW)
        normal = Task(title="Обычно", priority=TaskPriority.NORMAL, status=TaskStatus.NEW)
        self.assertGreater(rank_task(critical, now)[0], rank_task(normal, now)[0])
