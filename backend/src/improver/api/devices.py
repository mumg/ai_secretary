from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.db import get_session
from improver.models import Device
from improver.schemas import DeviceRead, DeviceUpsert

router = APIRouter(prefix="/devices", tags=["devices"])


@router.put("/current", response_model=DeviceRead)
async def upsert_current_device(
    payload: DeviceUpsert,
    session: AsyncSession = Depends(get_session),
) -> Device:
    device = await session.scalar(select(Device).where(Device.fcm_token == payload.fcm_token))
    if device is None:
        device = Device(label=payload.label, fcm_token=payload.fcm_token)
        session.add(device)
    else:
        device.label = payload.label
        device.active = True
    device.last_seen_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(device)
    return device
