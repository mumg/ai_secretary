from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.api.tasks import reject_task
from improver.config import AppConfig
from improver.models import Base, DailyPlanItem, Reminder, Task
from improver.services.plans import rebuild_plan


@skipUnless(find_spec("aiosqlite"), "aiosqlite is required for isolated database tests")
class TaskRejectionTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.config = AppConfig(server={"timezone": "UTC"})
        patcher = patch("improver.services.plans.enqueue_today_contexts", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def test_reject_all_open_states_disables_reminders_and_removes_plan_items(self):
        for status in ["NEEDS_CONFIRMATION", "NEW", "IN_PROGRESS", "POSSIBLY_COMPLETED"]:
            with self.subTest(status=status):
                task = Task(title=f"Task {status}", status=status)
                self.session.add(task)
                await self.session.flush()
                reminder = Reminder(
                    task_id=task.id,
                    remind_at=datetime.now(UTC) + timedelta(hours=1),
                    enabled=True,
                )
                self.session.add(reminder)
                await rebuild_plan(self.session, datetime.now(UTC))
                await self.session.commit()
                result = await reject_task(task.id, self.session, self.config)
                self.assertEqual(result.status, "CANCELLED")
                self.assertIsNone(result.completed_at)
                self.assertFalse(result.reminders[0].enabled)
                count = await self.session.scalar(
                    select(func.count())
                    .select_from(DailyPlanItem)
                    .where(DailyPlanItem.task_id == task.id)
                )
                self.assertEqual(count, 0)
                # A retried POST remains successful and does not reopen the task.
                self.assertEqual(
                    (await reject_task(task.id, self.session, self.config)).status, "CANCELLED"
                )

    async def test_completed_task_is_not_rewritten_as_rejected(self):
        task = Task(title="Completed", status="COMPLETED", completed_at=datetime.now(UTC))
        self.session.add(task)
        await self.session.commit()
        with self.assertRaises(HTTPException) as error:
            await reject_task(task.id, self.session, self.config)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(task.status, "COMPLETED")
