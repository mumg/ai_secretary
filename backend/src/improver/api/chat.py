import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.models import ChatRequest
from improver.schemas import ChatQuery, ChatRequestRead, ChatResponse
from improver.services.archive_chat import ArchiveChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/requests",
    response_model=ChatRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_archive_query(
    payload: ChatQuery,
    session: AsyncSession = Depends(get_session),
) -> ChatRequest:
    request = ChatRequest(
        query=payload.query,
        history=[message.model_dump() for message in payload.history],
        tag_ids=[str(tag_id) for tag_id in payload.tag_ids],
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


@router.get("/requests", response_model=list[ChatRequestRead])
async def list_archive_queries(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ChatRequest]:
    result = await session.execute(
        select(ChatRequest).order_by(ChatRequest.created_at.desc()).limit(limit)
    )
    return list(reversed(list(result.scalars())))


@router.get("/requests/{request_id}", response_model=ChatRequestRead)
async def get_archive_query(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ChatRequest:
    request = await session.get(ChatRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Chat request not found")
    return request


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
