"""Read-only release checks, independent of the database and event processing."""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx
from pydantic import BaseModel

from improver import __version__

VERSION_URL = "https://raw.githubusercontent.com/mumg/ai_secretary/main/version"
REPOSITORY_URL = "https://github.com/mumg/ai_secretary"
CHECK_INTERVAL = 6 * 60 * 60
RETRY_INTERVAL = 15 * 60
MANUAL_INTERVAL = 60


def version_number(value: str) -> tuple[int, int, int]:
    """The release file contains one stable MAJOR.MINOR.PATCH version."""
    if not re.fullmatch(r"(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})", value):
        raise ValueError("Версия должна иметь формат MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in value.split("."))


class VersionStatus(BaseModel):
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    checked_at: datetime | None = None
    last_success_at: datetime | None = None
    next_check_at: datetime | None = None
    error: str | None = None
    repository_url: str = REPOSITORY_URL


class UpdateChecker:
    def __init__(self, current_version: str = __version__):
        self.status = VersionStatus(current_version=current_version)
        self._lock = asyncio.Lock()
        self._next_due = 0.0
        self._manual_due = 0.0

    async def check(
        self, *, manual: bool = False, client: httpx.AsyncClient | None = None
    ) -> VersionStatus:
        async with self._lock:
            now = monotonic()
            if now < (self._manual_due if manual else self._next_due):
                return self.status.model_copy()
            checked_at = datetime.now(UTC)
            error = None
            try:
                current = version_number(self.status.current_version)
                if client is None:
                    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
                        latest = await self._fetch(http)
                else:
                    latest = await self._fetch(client)
                self.status.latest_version = latest
                self.status.update_available = version_number(latest) > current
                self.status.last_success_at = checked_at
            except httpx.HTTPStatusError as exc:
                error = (
                    "Файл version ещё не опубликован в ветке main на GitHub."
                    if exc.response.status_code == 404
                    else "GitHub временно недоступен. Проверка будет повторена."
                )
            except (httpx.HTTPError, TimeoutError):
                error = "Не удалось связаться с GitHub. Проверка будет повторена."
            except (ValueError, UnicodeError):
                error = "Некорректный файл версии. Ожидается MAJOR.MINOR.PATCH."
            interval = RETRY_INTERVAL if error else CHECK_INTERVAL
            # Preserve the last confirmed release if a subsequent request fails.
            self.status.checked_at = checked_at
            self.status.error = error
            self.status.next_check_at = checked_at + timedelta(seconds=interval)
            self._next_due = monotonic() + interval
            self._manual_due = monotonic() + MANUAL_INTERVAL
            return self.status.model_copy()

    @staticmethod
    async def _fetch(client: httpx.AsyncClient) -> str:
        async with asyncio.timeout(15):
            async with client.stream("GET", VERSION_URL, headers={
                "Accept": "text/plain", "User-Agent": "AI-Secretary-Version-Check",
                "Cache-Control": "no-cache",
            }) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 128:
                        raise ValueError("Version response too large")
        value = content.decode("utf-8").strip()
        version_number(value)
        return value

    async def run(self) -> None:
        while True:
            await self.check()
            await asyncio.sleep(60)


update_checker = UpdateChecker()
