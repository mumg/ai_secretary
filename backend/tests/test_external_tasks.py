"""External task API tests require an isolated migrated PostgreSQL database."""

import os
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.api.external_tasks import upsert_external_tasks
from improver.config import AppConfig, SourceConfig
from improver.connectors import connector_for
from improver.enums import TaskStatus
from improver.models import CommunicationEvent, CommunicationSource, Task
from improver.schemas import ExternalTaskBatchWrite, ExternalTaskWrite

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


class ExternalTaskContractTests(TestCase):
    def test_routes_are_exposed_in_openapi(self) -> None:
        from improver.main import app

        paths = app.openapi()["paths"]

        self.assertIn("/api/v1/external-task-sources/{source_id}", paths)
        self.assertIn("/api/v1/external-task-sources/{source_id}/tasks:batch", paths)

    def test_duplicate_external_ids_are_rejected(self) -> None:
        item = {"external_id": "ISSUE-1", "title": "Check integration"}

        with self.assertRaises(ValidationError):
            ExternalTaskBatchWrite(tasks=[item, item])

    def test_external_source_is_passive(self) -> None:
        config = AppConfig()
        source = SourceConfig(id="jira", type="external_tasks")

        connector = connector_for(source, config)

        self.assertEqual(connector.__class__.__name__, "ExternalTaskConnector")


@skipUnless(os.getenv("EXTERNAL_TASK_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class ExternalTaskIntegrationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(os.environ["EXTERNAL_TASK_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.factory = async_sessionmaker(
            self.connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        self.config = AppConfig(server={"timezone": "UTC"})
        async with self.factory() as session:
            session.add(
                CommunicationSource(
                    id="external-synthetic",
                    label="Synthetic tracker",
                    source_type="external_tasks",
                    settings={},
                )
            )
            await session.commit()
        patcher = patch(
            "improver.api.external_tasks.NotificationService.send",
            new=AsyncMock(return_value=True),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    def payload(self, **values) -> ExternalTaskBatchWrite:
        item = {
            "external_id": "ISSUE-1",
            "title": "Check integration",
            "description": "Synthetic context",
            "priority": "HIGH",
            "source_url": "https://tracker.example.test/ISSUE-1",
            "occurred_at": NOW,
            "source_updated_at": NOW,
        }
        item.update(values)
        return ExternalTaskBatchWrite(tasks=[ExternalTaskWrite(**item)])

    async def sync(self, payload: ExternalTaskBatchWrite):
        async with self.factory() as session:
            return await upsert_external_tasks(
                "external-synthetic", payload, session, self.config
            )

    async def test_upsert_creates_one_active_task_and_is_idempotent(self) -> None:
        created = await self.sync(self.payload())
        repeated = await self.sync(self.payload())

        self.assertEqual(created.created, 1)
        self.assertTrue(created.items[0].active)
        self.assertEqual(repeated.unchanged, 1)
        self.assertTrue(repeated.items[0].active)
        async with self.factory() as session:
            tasks = list((await session.execute(select(Task))).scalars())
            event = await session.scalar(select(CommunicationEvent))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].priority, "HIGH")
        self.assertEqual(event.analysis_state, "SKIPPED")

    async def test_unchanged_source_reports_locally_closed_task_as_inactive(self) -> None:
        await self.sync(self.payload())
        async with self.factory() as session:
            task = await session.scalar(select(Task))
            task.status = TaskStatus.COMPLETED
            task.completed_at = NOW
            await session.commit()

        result = await self.sync(self.payload())

        self.assertEqual(result.unchanged, 1)
        self.assertEqual(result.items[0].status, TaskStatus.COMPLETED)
        self.assertFalse(result.items[0].active)

    async def test_new_source_revision_updates_task(self) -> None:
        await self.sync(self.payload())

        result = await self.sync(
            self.payload(
                title="Check updated integration",
                status="IN_PROGRESS",
                source_updated_at=NOW + timedelta(minutes=1),
            )
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(result.items[0].status, TaskStatus.IN_PROGRESS)
        self.assertTrue(result.items[0].active)

    async def test_complete_snapshot_closes_missing_active_task(self) -> None:
        await self.sync(self.payload())

        result = await self.sync(ExternalTaskBatchWrite(tasks=[], close_missing=True))

        self.assertEqual(result.closed_missing, 1)
        async with self.factory() as session:
            task = await session.scalar(select(Task))
        self.assertEqual(task.status, TaskStatus.CANCELLED)
