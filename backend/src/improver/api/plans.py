from __future__ import annotations

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.api.deps import get_runtime_config
from improver.config import AppConfig
from improver.db import get_session
from improver.models import CommunicationSource
from improver.schemas import DailyPlanRead
from improver.services.calendar import BusinessCalendar
from improver.services.meetings import meeting_read, meetings_between
from improver.services.plans import get_plan, rebuild_plan

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/today", response_model=DailyPlanRead)
async def today_plan(
    refresh: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    config: AppConfig = Depends(get_runtime_config),
) -> DailyPlanRead:
    now = BusinessCalendar(config).now()
    plan = None if refresh else await get_plan(session, now.date())
    if plan is None:
        plan = await rebuild_plan(session, now)
        await session.commit()
        plan = await get_plan(session, now.date())
    assert plan is not None
    day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    meetings = await meetings_between(session, day_start + timedelta(days=1), now)
    source_ids = {meeting.source_id for meeting in meetings}
    labels = dict(
        (
            await session.execute(
                select(CommunicationSource.id, CommunicationSource.label).where(
                    CommunicationSource.id.in_(source_ids)
                )
            )
        ).all()
    )
    return DailyPlanRead(
        id=plan.id,
        plan_date=plan.plan_date,
        generated_at=plan.generated_at,
        items=plan.items,
        meetings=[meeting_read(meeting, labels.get(meeting.source_id)) for meeting in meetings],
    )
