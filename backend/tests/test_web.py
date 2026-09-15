"""Public UI contract and pagination against a disposable migrated database."""

import os
import uuid
from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, skipUnless

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.main import app
from improver.models import ChatRequest, CommunicationEvent, CommunicationSource, ConversationThread


class WebRouteTests(IsolatedAsyncioTestCase):
    async def test_cross_site_form_posts_are_rejected_before_reaching_the_archive(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/tasks", headers={"Origin": "https://evil.test"})
            self.assertEqual(response.status_code, 403)
            response = await client.post("/api/v1/tasks", headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(response.status_code, 403)
            # Same-origin and native callers pass the origin gate (unknown route: 404).
            for headers in ({"Origin": "https://test", "Sec-Fetch-Site": "same-origin"}, {}):
                self.assertEqual((await client.post("/missing", headers=headers)).status_code, 404)

    async def test_shell_assets_and_minimal_config(self):
        config = AppConfig()
        config.server.timezone = "Asia/Tokyo"
        app.dependency_overrides[get_runtime_config] = lambda: config
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                for path in ("/", "/app", "/app/"):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('id="reading-pane"', response.text)
                    self.assertIn('id="list-scroll"', response.text)
                    self.assertNotIn("<script>", response.text)
                for name in ("app.js", "app-core.js", "app.css", "markdown-it.min.js"):
                    self.assertEqual((await client.get(f"/app/assets/{name}")).status_code, 200)
                self.assertEqual(
                    (await client.get("/api/v1/ui/config")).json(), {"timezone": "Asia/Tokyo"}
                )
                self.assertEqual((await client.get("/admin")).status_code, 200)
        finally:
            app.dependency_overrides.clear()


@skipUnless(os.getenv("WEB_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class ThreadPaginationTests(IsolatedAsyncioTestCase):
    async def test_thread_pages_keep_equal_timestamp_order_and_exclude_calendar_events(self):
        engine = create_async_engine(os.environ["WEB_TEST_DATABASE_URL"])
        async with engine.connect() as connection:
            transaction = await connection.begin()
            factory = async_sessionmaker(
                connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as session:
                now = datetime.now(UTC)
                source = CommunicationSource(
                    id="web-synthetic", label="Synthetic", source_type="imap"
                )
                session.add(source)
                await session.flush()
                thread = ConversationThread(
                    source_id=source.id,
                    source_type="imap",
                    thread_external_id="thread",
                    title="Synthetic",
                    participants=[],
                    summary="**Резюме**",
                    event_count=66,
                    first_event_at=now,
                    last_event_at=now,
                )
                session.add(thread)
                events = []
                for index in range(66):
                    event = CommunicationEvent(
                        id=uuid.uuid4(),
                        source_id=source.id,
                        source_type="imap",
                        external_id=str(index),
                        thread_external_id="thread",
                        event_type="email",
                        subject=f"Message {index}",
                        body=(
                            f"Original message {index}\n\nDetails from the sender." if index else ""
                        ),
                        semantic_summary="Repeated Qwen summary",
                        occurred_at=now,
                        participants=[],
                        analysis_state="COMPLETED",
                        is_mailing=False,
                        content_hash=uuid.uuid4().hex,
                    )
                    events.append(event)
                session.add(
                    CommunicationEvent(
                        source_id=source.id,
                        source_type="imap",
                        external_id="calendar",
                        thread_external_id="thread",
                        event_type="meeting_invitation",
                        subject="Invitation",
                        body="Synthetic",
                        occurred_at=now,
                        participants=[],
                        analysis_state="COMPLETED",
                        is_mailing=False,
                        content_hash=uuid.uuid4().hex,
                    )
                )
                chat_rows = [
                    ChatRequest(
                        id=uuid.uuid4(),
                        query=f"Synthetic {i}",
                        history=[],
                        references=[],
                        tag_ids=[],
                        created_at=now,
                    )
                    for i in range(66)
                ]
                session.add_all(chat_rows)
                session.add_all(events)
                await session.commit()
                expected = [
                    str(item.id) for item in sorted(events, key=lambda x: x.id, reverse=True)
                ]
                thread_id = thread.id

            async def sessions():
                async with factory() as session:
                    yield session

            app.dependency_overrides[get_session] = sessions
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    ids = []
                    for offset, more in ((0, True), (30, True), (60, False)):
                        response = await client.get(
                            f"/api/v1/threads/{thread_id}",
                            params={"events_offset": offset, "events_limit": 30},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        page = response.json()
                        self.assertEqual(page["has_more_events"], more)
                        self.assertEqual(page["event_count"], 66)
                        ids.extend(row["id"] for row in page["events"])
                        originals = {str(event.id): event.body for event in events}
                        for row in page["events"]:
                            self.assertEqual(row["preview"], originals[row["id"]])
                            self.assertNotIn("Repeated Qwen summary", row["preview"])
                    self.assertEqual(ids, expected)
                    chat_ids = []
                    before = None
                    for _ in range(3):
                        params = {"limit": 30}
                        if before:
                            params["before"] = before
                        response = await client.get("/api/v1/chat/requests", params=params)
                        self.assertEqual(response.status_code, 200)
                        page = response.json()
                        chat_ids = [row["id"] for row in page] + chat_ids
                        before = page[0]["id"]
                    self.assertEqual(chat_ids, sorted(str(row.id) for row in chat_rows))
                    self.assertEqual(
                        (
                            await client.get(
                                f"/api/v1/threads/{thread_id}", params={"events_limit": 101}
                            )
                        ).status_code,
                        422,
                    )
            finally:
                app.dependency_overrides.clear()
                await transaction.rollback()
        await engine.dispose()
