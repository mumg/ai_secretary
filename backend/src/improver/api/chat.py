import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.schemas import ChatQuery, ChatResponse
from improver.services.archive_chat import ArchiveChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
async def query_archive(
    payload: ChatQuery,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> ChatResponse:
    answer, references = await ArchiveChatService(session, config).answer(
        query=payload.query,
        history=payload.history,
        tag_ids=payload.tag_ids,
    )
    return ChatResponse(answer=answer, references=references)


@router.post("/stream")
async def stream_archive(
    payload: ChatQuery,
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> StreamingResponse:
    async def event_stream():
        try:
            async for item in ArchiveChatService(session, config).stream_answer(
                query=payload.query,
                history=payload.history,
                tag_ids=payload.tag_ids,
            ):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except Exception as exc:
            error = {
                "type": "error",
                "message": str(exc)[:1000] or "Не удалось получить ответ от Qwen",
            }
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
