from __future__ import annotations

import asyncio
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from improver.services.realtime import hub

router = APIRouter()


def allowed_origin(origin: str | None, host: str) -> bool:
    # Native mTLS clients omit Origin. Browser cookies/certificates must not make
    # a socket opened by another site an authenticated archive subscription.
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() == host.lower()
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    if not allowed_origin(websocket.headers.get("origin"), websocket.headers.get("host", "")):
        await websocket.close(code=1008)
        return
    if not hub.ready:
        await websocket.close(code=1013)
        return
    await websocket.accept()

    async def receive() -> None:
        while True:
            # Application heartbeat also detects half-open browser connections.
            message = await asyncio.wait_for(websocket.receive_text(), timeout=65)
            if message != "pong":
                await websocket.close(code=1008)
                return

    async def send(subscriber) -> None:
        while hub.ready:
            try:
                topics = await asyncio.wait_for(subscriber.take(), timeout=20)
                await asyncio.wait_for(
                    websocket.send_json({"type": "changed", "topics": topics}), 10
                )
            except TimeoutError:
                await asyncio.wait_for(websocket.send_json({"type": "ping"}), 10)
            # Busy feeds must still solicit a pong; send with each batched signal.
            else:
                await asyncio.wait_for(websocket.send_json({"type": "ping"}), 10)
        await websocket.close(code=1013)

    async with hub.subscribe() as subscriber:
        jobs = [asyncio.create_task(receive()), asyncio.create_task(send(subscriber))]
        try:
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                job.result()
        except (WebSocketDisconnect, TimeoutError, OSError, RuntimeError):
            pass
        finally:
            for job in jobs:
                job.cancel()
            for job in jobs:
                with suppress(
                    asyncio.CancelledError, WebSocketDisconnect, TimeoutError, OSError, RuntimeError
                ):
                    await job
            with suppress(WebSocketDisconnect, RuntimeError, OSError):
                await websocket.close()
