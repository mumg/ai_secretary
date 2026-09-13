from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig
from improver.db import get_session
from improver.services.settings import load_runtime_config


async def get_runtime_config(
    session: AsyncSession = Depends(get_session),
) -> AppConfig:
    return await load_runtime_config(session)
