from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from improver.enums import TaskStatus
from improver.models import DailyPlan, DailyPlanItem, Task
from improver.services.meeting_context import enqueue_today_contexts
from improver.services.ranking import rank_task, task_sort_key

ACTIVE_PLAN_STATUSES = {
    TaskStatus.NEW,
    TaskStatus.IN_PROGRESS,
    TaskStatus.POSSIBLY_COMPLETED,
}


async def rerank_active_tasks(session: AsyncSession, now: datetime) -> list[Task]:
    result = await session.execute(
        select(Task)
        .where(Task.status.in_([status.value for status in ACTIVE_PLAN_STATUSES]))
        .options(selectinload(Task.reminders))
    )
    tasks = list(result.scalars().unique())
    for task in tasks:
        task.ranking_score, task.ranking_reasons = rank_task(task, now)
    tasks.sort(key=task_sort_key)
    await session.flush()
    return tasks


async def rebuild_plan(session: AsyncSession, now: datetime) -> DailyPlan:
    tasks = await rerank_active_tasks(session, now)
    result = await session.execute(select(DailyPlan).where(DailyPlan.plan_date == now.date()))
    plan = result.scalar_one_or_none()
    if plan is None:
        plan = DailyPlan(plan_date=now.date(), generated_at=now)
        session.add(plan)
        await session.flush()
    else:
        await session.execute(
            delete(DailyPlanItem)
            .where(DailyPlanItem.plan_id == plan.id)
            .execution_options(synchronize_session=False)
        )
        plan.generated_at = now

    session.add_all(
        [
            DailyPlanItem(
                plan_id=plan.id,
                task_id=task.id,
                position=position,
                automatically_added=True,
            )
            for position, task in enumerate(tasks, start=1)
        ]
    )
    await session.flush()
    await enqueue_today_contexts(session, now)
    return plan


async def get_plan(session: AsyncSession, plan_date: date) -> DailyPlan | None:
    result = await session.execute(
        select(DailyPlan)
        .where(DailyPlan.plan_date == plan_date)
        .options(
            selectinload(DailyPlan.items)
            .selectinload(DailyPlanItem.task)
            .selectinload(Task.reminders)
        )
    )
    return result.scalar_one_or_none()
