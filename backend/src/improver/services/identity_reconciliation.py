from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import IdentityConfig
from improver.enums import AnalysisState
from improver.models import CommunicationEvent
from improver.services.assignment import assignment_signals
from improver.services.text import clean_email_body


def _previously_eligible(event: CommunicationEvent) -> bool:
    result = event.analysis_result if isinstance(event.analysis_result, dict) else {}
    signals = result.get("assignment_signals")
    return bool(isinstance(signals, dict) and signals.get("eligible") is True)


def _currently_eligible(event: CommunicationEvent, identity: IdentityConfig) -> bool:
    original_body = event.body
    try:
        if event.event_type == "email":
            event.body = clean_email_body(event.body)
        return assignment_signals(event, identity).eligible
    finally:
        event.body = original_body


async def requeue_newly_eligible_events(
    session: AsyncSession,
    identity: IdentityConfig,
) -> int:
    """Reanalyse only messages whose assignment eligibility changed to true."""
    result = await session.execute(
        select(CommunicationEvent)
        .where(
            CommunicationEvent.analysis_state == AnalysisState.COMPLETED,
            CommunicationEvent.analysis_model.is_not(None),
            CommunicationEvent.is_mailing.is_(False),
        )
        .with_for_update(skip_locked=True)
    )
    requeued = 0
    for event in result.scalars():
        if _previously_eligible(event) or not _currently_eligible(event, identity):
            continue
        event.analysis_state = AnalysisState.PENDING
        event.analysis_error = None
        event.next_analysis_at = None
        event.analysis_attempts = 0
        requeued += 1
    return requeued
