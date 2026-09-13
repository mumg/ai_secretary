from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AnalysisFilterConfig
from improver.enums import AnalysisState
from improver.models import CommunicationEvent, ConversationThread
from improver.services.analysis_filters import (
    apply_filtered_index,
    filtered_state,
    match_analysis_filter,
    reset_filtered_event,
)
from improver.services.meetings import MEETING_EVENT_TYPE


@dataclass
class FilterReconciliationStats:
    scanned: int = 0
    skipped: int = 0
    ignored: int = 0
    requeued: int = 0

    def model_dump(self) -> dict[str, int]:
        return asdict(self)


REBUILD_THREADS_SQL = """
WITH ranked AS (
    SELECT
        event.*,
        row_number() OVER (
            PARTITION BY event.source_id, event.thread_external_id
            ORDER BY event.occurred_at DESC, event.id DESC
        ) AS position,
        min(event.occurred_at) OVER (
            PARTITION BY event.source_id, event.thread_external_id
        ) AS first_event_at,
        count(*) OVER (
            PARTITION BY event.source_id, event.thread_external_id
        ) AS event_count
    FROM communication_events AS event
    WHERE event.analysis_state NOT IN ('SKIPPED', 'IGNORED')
      AND event.is_mailing = false
      AND event.event_type != 'meeting_invitation'
), summaries AS (
    SELECT DISTINCT ON (event.source_id, event.thread_external_id)
        event.source_id,
        event.thread_external_id,
        event.semantic_summary,
        event.analysis_model,
        event.analyzed_at
    FROM communication_events AS event
    WHERE event.analysis_state = 'COMPLETED'
      AND event.is_mailing = false
      AND event.event_type != 'meeting_invitation'
      AND NULLIF(btrim(event.semantic_summary), '') IS NOT NULL
    ORDER BY event.source_id, event.thread_external_id, event.occurred_at DESC, event.id DESC
)
INSERT INTO conversation_threads (
    id, source_id, source_type, thread_external_id, title, participants,
    summary, event_count, first_event_at, last_event_at, latest_event_id,
    summary_model, summarized_at
)
SELECT
    gen_random_uuid(), ranked.source_id, ranked.source_type, ranked.thread_external_id,
    ranked.subject, ranked.participants, summaries.semantic_summary, ranked.event_count,
    ranked.first_event_at, ranked.occurred_at, ranked.id,
    summaries.analysis_model, summaries.analyzed_at
FROM ranked
LEFT JOIN summaries
  ON summaries.source_id = ranked.source_id
 AND summaries.thread_external_id = ranked.thread_external_id
WHERE ranked.position = 1
"""


async def rebuild_filtered_threads(session: AsyncSession) -> None:
    await session.execute(delete(ConversationThread))
    await session.execute(text(REBUILD_THREADS_SQL))


async def reconcile_analysis_filters(
    session: AsyncSession,
    filters: AnalysisFilterConfig,
) -> FilterReconciliationStats:
    result = await session.execute(
        select(CommunicationEvent)
        .where(CommunicationEvent.analysis_state != AnalysisState.PROCESSING)
        .with_for_update()
    )
    events = list(result.scalars())
    stats = FilterReconciliationStats(scanned=len(events))
    for event in events:
        if event.event_type == MEETING_EVENT_TYPE:
            continue
        match = match_analysis_filter(event, filters)
        if match is not None:
            target_state = filtered_state(match)
            if event.analysis_state != target_state:
                if target_state == AnalysisState.IGNORED:
                    stats.ignored += 1
                else:
                    stats.skipped += 1
            apply_filtered_index(event, match)
            event.analysis_state = target_state
            event.analysis_error = None
            event.analysis_model = None
            event.analysis_result = {
                "skipped": True,
                "technical": match.technical,
                "filter": {"kind": match.kind, "value": match.value},
            }
            event.analyzed_at = datetime.now(UTC)
            continue

        if event.analysis_state in {AnalysisState.SKIPPED, AnalysisState.IGNORED}:
            reset_filtered_event(event)
            stats.requeued += 1

    await rebuild_filtered_threads(session)
    return stats
