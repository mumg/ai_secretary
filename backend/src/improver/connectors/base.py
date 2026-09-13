from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig, SourceConfig


class SourceConnector(ABC):
    def __init__(self, source: SourceConfig, config: AppConfig):
        self.source = source
        self.config = config

    @abstractmethod
    async def sync(self, session: AsyncSession) -> int:
        """Fetch and persist new communication events."""

    async def test_connection(self) -> dict[str, str]:
        raise NotImplementedError(f"Connection test is unavailable for {self.source.type}")
