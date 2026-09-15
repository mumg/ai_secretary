"""One PostgreSQL listener per API process; bounded coalescing per client."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

import asyncpg
import structlog
from sqlalchemy.engine import make_url

from improver.config import get_config

log = structlog.get_logger()
TOPICS = frozenset(
    {"all", "tasks", "meetings", "contexts", "results", "threads", "events", "chat", "status"}
)


class Subscription:
    def __init__(self):
        self.pending: set[str] = set()
        self.changed = asyncio.Event()

    def put(self, topic: str) -> None:
        self.pending.add(topic)
        self.changed.set()

    async def take(self) -> list[str]:
        await self.changed.wait()
        # Batch importer bursts without allocating an unbounded message queue.
        await asyncio.sleep(0.2)
        topics = sorted(self.pending)
        self.pending.clear()
        self.changed.clear()
        return topics


class RealtimeHub:
    def __init__(self):
        self.subscribers: set[Subscription] = set()
        self.ready = False

    def publish(self, topic: str) -> None:
        if topic in TOPICS:
            for subscriber in self.subscribers:
                subscriber.put(topic)

    @asynccontextmanager
    async def subscribe(self):
        subscriber = Subscription()
        self.subscribers.add(subscriber)
        subscriber.put("all")  # A reconnect always reconciles missed changes.
        try:
            yield subscriber
        finally:
            self.subscribers.discard(subscriber)

    async def run(self, check_interval: float = 20) -> None:
        url = make_url(get_config().database.resolved_url())
        dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
        backoff = 1
        while True:
            connection = None
            try:
                connection = await asyncpg.connect(
                    dsn,
                    timeout=10,
                    command_timeout=10,
                    server_settings={"application_name": "improver-realtime"},
                )
                await connection.add_listener(
                    "improver_changes", lambda _c, _p, _ch, topic: self.publish(topic)
                )
                self.ready = True
                self.publish("all")
                backoff = 1
                ticks = 0
                while True:
                    await asyncio.sleep(check_interval)
                    await connection.execute("SELECT 1")
                    # Metrics such as lock occupancy, service reachability and TTL
                    # expiry can change without a write to the archive.
                    ticks += 1
                    self.publish("all" if ticks % 15 == 0 else "status")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("realtime_listener_reconnecting", error_type=type(exc).__name__)
            finally:
                self.ready = False
                if connection is not None:
                    with suppress(Exception):
                        await connection.close(timeout=2)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


hub = RealtimeHub()
