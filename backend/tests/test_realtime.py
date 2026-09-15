import asyncio
import os
import uuid
from contextlib import suppress
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless

import asyncpg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from improver.api.realtime import allowed_origin, router
from improver.services.realtime import RealtimeHub, hub


class RealtimeOriginTests(TestCase):
    def test_native_and_same_origin_allowed_cross_origin_rejected(self):
        self.assertTrue(allowed_origin(None, "secretary.example"))
        self.assertTrue(allowed_origin("https://secretary.example", "secretary.example"))
        for origin in (
            "null",
            "https://evil.example",
            "https://secretary.example.evil",
            "file://secretary.example",
            "https://user@secretary.example",
        ):
            self.assertFalse(allowed_origin(origin, "secretary.example"))

    def test_websocket_resync_heartbeat_and_origin_gate(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        hub.ready = True
        try:
            with TestClient(app) as client:
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect(
                        "/api/v1/realtime", headers={"origin": "https://evil.test"}
                    ):
                        pass
                with client.websocket_connect(
                    "/api/v1/realtime", headers={"origin": "http://testserver"}
                ) as ws:
                    self.assertEqual(ws.receive_json(), {"type": "changed", "topics": ["all"]})
                    self.assertEqual(ws.receive_json(), {"type": "ping"})
                    ws.send_text("pong")
                    client.portal.call(hub.publish, "tasks")
                    self.assertEqual(ws.receive_json(), {"type": "changed", "topics": ["tasks"]})
                    self.assertEqual(ws.receive_json(), {"type": "ping"})
                    ws.send_text("not-a-protocol-message")
                    with self.assertRaises(WebSocketDisconnect):
                        ws.receive_json()
                hub.ready = False
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect("/api/v1/realtime"):
                        pass
            self.assertFalse(hub.subscribers)
        finally:
            hub.ready = False


class RealtimeHubTests(IsolatedAsyncioTestCase):
    async def test_bursts_coalesce_and_independent_subscribers_cleanup(self):
        local = RealtimeHub()
        async with local.subscribe() as first, local.subscribe() as second:
            await first.take()
            await second.take()
            for _ in range(10000):
                local.publish("tasks")
                local.publish("chat")
            local.publish("not-a-topic")
            self.assertEqual(first.pending, {"tasks", "chat"})
            self.assertEqual(await first.take(), ["chat", "tasks"])
            self.assertEqual(second.pending, {"tasks", "chat"})
        self.assertFalse(local.subscribers)


@skipUnless(os.getenv("REALTIME_TEST_DATABASE_URL"), "isolated migrated PostgreSQL URL not set")
class RealtimeDatabaseTests(IsolatedAsyncioTestCase):
    async def test_commits_rollback_bulk_raw_sql_and_ranking_do_not_loop(self):
        dsn = os.environ["REALTIME_TEST_DATABASE_URL"].replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        listener = await asyncpg.connect(dsn)
        writer = await asyncpg.connect(dsn)
        queue = asyncio.Queue()
        await listener.add_listener(
            "improver_changes", lambda _c, _p, _ch, topic: queue.put_nowait(topic)
        )
        ids = [uuid.uuid4() for _ in range(3)]
        try:
            tx = writer.transaction()
            await tx.start()
            for identity in ids:
                await writer.execute(
                    """INSERT INTO tasks (id,title,status,priority,priority_source,
                    ranking_score,ranking_reasons,manually_created)
                    VALUES ($1,'Synthetic','NEW','NORMAL','MANUAL',0,'[]',true)""",
                    identity,
                )
            await asyncio.sleep(0.05)
            self.assertTrue(queue.empty(), "must not announce uncommitted data")
            await tx.rollback()
            await asyncio.sleep(0.05)
            self.assertTrue(queue.empty(), "rollback must not emit")
            async with writer.transaction():
                for identity in ids:
                    await writer.execute(
                        """INSERT INTO tasks (id,title,status,priority,priority_source,
                        ranking_score,ranking_reasons,manually_created)
                        VALUES ($1,'Synthetic','NEW','NORMAL','MANUAL',0,'[]',true)""",
                        identity,
                    )
            self.assertEqual(await asyncio.wait_for(queue.get(), 2), "tasks")
            await asyncio.sleep(0.05)
            self.assertTrue(queue.empty(), "bulk transaction must coalesce")
            await writer.execute(
                "UPDATE tasks SET ranking_score=99, updated_at=now() WHERE id=ANY($1::uuid[])", ids
            )
            await asyncio.sleep(0.05)
            self.assertTrue(queue.empty(), "read-time ranking must not feed back")
            await writer.execute("UPDATE tasks SET status='COMPLETED' WHERE id=$1", ids[0])
            self.assertEqual(await asyncio.wait_for(queue.get(), 2), "tasks")
        finally:
            await writer.execute("DELETE FROM tasks WHERE id=ANY($1::uuid[])", ids)
            await listener.close()
            await writer.close()

    async def test_listener_recovers_and_resyncs_after_database_disconnect(self):
        local = RealtimeHub()
        job = asyncio.create_task(local.run(check_interval=0.1))
        dsn = os.environ["REALTIME_TEST_DATABASE_URL"].replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        admin = await asyncpg.connect(dsn)
        try:
            for _ in range(50):
                if local.ready:
                    break
                await asyncio.sleep(0.1)
            self.assertTrue(local.ready)
            async with local.subscribe() as subscriber:
                self.assertIn("all", await subscriber.take())
                await admin.execute("""SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                    WHERE application_name='improver-realtime' AND datname=current_database()""")
                for _ in range(20):
                    if not local.ready:
                        break
                    await asyncio.sleep(0.05)
                self.assertFalse(local.ready)
                async with asyncio.timeout(4):
                    while "all" not in await subscriber.take():
                        pass
                self.assertTrue(local.ready)
        finally:
            await admin.close()
            job.cancel()
            with suppress(asyncio.CancelledError):
                await job
        self.assertFalse(local.ready)
