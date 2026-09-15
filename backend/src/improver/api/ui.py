from fastapi import APIRouter, Depends

from improver.api.deps import get_runtime_config
from improver.config import AppConfig

router = APIRouter(prefix="/ui", tags=["web"])


@router.get("/config")
async def ui_config(config: AppConfig = Depends(get_runtime_config)) -> dict[str, str]:
    """Only presentation settings; never expose connector configuration or credentials."""
    return {"timezone": config.server.timezone}
