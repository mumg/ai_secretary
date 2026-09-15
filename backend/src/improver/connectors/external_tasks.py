from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from improver.connectors.base import SourceConnector


class ExternalTaskConnector(SourceConnector):
    """Passive source populated through the external task ingestion API."""

    async def sync(self, session: AsyncSession) -> int:
        return 0

    async def test_connection(self) -> dict[str, str]:
        return {"status": "ok", "detail": "External task API is ready"}
