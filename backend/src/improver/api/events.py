from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from improver.db import get_session
from improver.models import CommunicationEvent
from improver.schemas import EventIngest, EventRead

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/{event_id}", response_model=EventRead)
async def get_event(
    event_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> CommunicationEvent:
    event = await session.scalar(
        select(CommunicationEvent).where(CommunicationEvent.id == event_id)
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("", response_model=EventRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    payload: EventIngest, session: AsyncSession = Depends(get_session)
) -> CommunicationEvent:
    digest = hashlib.sha256(
        "\0".join(
            [
                payload.source_id,
                payload.external_id,
                payload.author or "",
                payload.occurred_at.isoformat(),
                payload.body,
            ]
        ).encode("utf-8")
    ).hexdigest()
    event = CommunicationEvent(**payload.model_dump(), content_hash=digest)
    session.add(event)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Event already exists") from None
    await session.refresh(event)
    return event
