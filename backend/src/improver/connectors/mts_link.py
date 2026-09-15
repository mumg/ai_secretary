from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig, SourceConfig
from improver.connectors.base import SourceConnector
from improver.enums import Direction
from improver.models import CommunicationEvent, MeetingResult
from improver.services.meeting_results import (
    MEETING_TRANSCRIPT_EVENT_TYPE,
    MTS_TRANSCRIPT_ORIGIN,
    attach_email_results_to_transcript,
    link_result_to_calendar,
)
from improver.services.mts_link import (
    find_mts_link_url_in_payload,
    mts_link_reference_keys,
)

log = structlog.get_logger()


class MtsLinkAuthError(RuntimeError):
    pass


class MtsLinkApiError(RuntimeError):
    def __init__(self, path: str, status_code: int):
        super().__init__(f"MTS Link {path} returned HTTP {status_code}")
        self.path = path
        self.status_code = status_code


def _parse_datetime(value: object, fallback: datetime) -> datetime:
    if not value:
        return fallback
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return fallback
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _meeting_interval(session_data: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """Use actual API session bounds, never room creation/estimated time as an end."""
    values = []
    for key in ("startsAt", "endsAt"):
        raw = session_data.get(key)
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if value.tzinfo is None:
            return None
        values.append(value)
    start, end = values
    return (start, end) if end > start else None


def _data_items(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _session_from_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    candidate = entry.get("data") or entry.get("eventSession") or entry
    return candidate if isinstance(candidate, dict) else None


def _format_transcript(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text:
            continue
        speaker = " ".join(str(item.get("nickname") or "Участник").split()).strip()
        timestamp = str(item.get("dateTime") or "").strip()
        try:
            time_label = _parse_datetime(timestamp, datetime.now(UTC)).strftime("%H:%M:%S")
        except (OverflowError, OSError):
            time_label = ""
        prefix = " · ".join(value for value in (time_label, speaker) if value)
        lines.append(f"{prefix}\n{text}" if prefix else text)
    return "\n\n".join(lines)


def _participants(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = " ".join(str(item.get("nickname") or "").split()).strip()
        user_id = str(item.get("userId") or "").strip()
        key = user_id or name.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        participant = {"name": name, "role": "speaker"}
        if user_id:
            participant["external_id"] = user_id
        result.append(participant)
    return result[:200]


class MtsLinkConnector(SourceConnector):
    def __init__(self, source: SourceConfig, config: AppConfig):
        super().__init__(source, config)
        self.base_url = (source.base_url or "https://gw.mts-link.ru").rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        token = (self.source.credential or "").strip()
        if token.casefold().startswith("bearer "):
            token = token[7:].strip()
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Improver/0.1 MTS-Link transcript connector",
            },
            cookies={"access": token},
            timeout=httpx.Timeout(45),
            follow_redirects=False,
        )

    @staticmethod
    async def _json(client: httpx.AsyncClient, path: str, **kwargs: object) -> object:
        response = await client.get(path, **kwargs)
        if response.status_code in {401, 403}:
            raise MtsLinkAuthError(
                "MTS Link rejected the access token; update it in the source settings"
            )
        if response.is_error:
            raise MtsLinkApiError(path, response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"MTS Link {path} returned invalid JSON") from exc

    async def _sessions(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        since = now - timedelta(days=self.config.communication_sources.initial_sync_days)
        schedule_params = {
            "eventType[0]": "meeting",
            "eventType[1]": "webinar",
            "eventType[2]": "training",
            "perPage": 100,
            "from": since.isoformat(),
            "to": now.isoformat(),
        }
        scheduled: list[dict[str, Any]] = []
        previous_signature: tuple[str, ...] | None = None
        for page in range(1, 101):
            page_items = _data_items(
                await self._json(
                    client,
                    "/api/eventsessions/schedule",
                    params={**schedule_params, "page": page},
                )
            )
            signature = tuple(
                str((_session_from_entry(item) or {}).get("id") or "")
                for item in page_items
            )
            if not page_items or signature == previous_signature:
                break
            scheduled.extend(page_items)
            previous_signature = signature
            if len(page_items) < 100:
                break

        endless: list[dict[str, Any]] = []
        previous_signature = None
        for page in range(1, 101):
            page_items = _data_items(
                await self._json(
                    client,
                    "/api/eventsessions/endless",
                    params={
                        "page": page,
                        "perPage": 100,
                        "filters[visibility][eq]": "visible",
                    },
                )
            )
            signature = tuple(
                str((_session_from_entry(item) or {}).get("id") or "")
                for item in page_items
            )
            if not page_items or signature == previous_signature:
                break
            endless.extend(page_items)
            previous_signature = signature
            if len(page_items) < 100:
                break
        sessions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for entry in [*scheduled, *endless]:
            session = _session_from_entry(entry)
            if session is None:
                continue
            event_session_id = str(session.get("id") or "").strip()
            activity_session_id = str(session.get("activitySessionId") or "").strip()
            if not event_session_id or (event_session_id, activity_session_id) in seen:
                continue
            seen.add((event_session_id, activity_session_id))
            session = {**session, "_entry": entry}
            sessions.append(session)
        return sessions

    async def _states(
        self,
        client: httpx.AsyncClient,
        sessions: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        by_key = {
            (str(item.get("id") or ""), str(item.get("activitySessionId") or "")): item
            for item in sessions
        }
        states: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for start in range(0, len(sessions), 30):
            params: list[tuple[str, str]] = []
            for index, item in enumerate(sessions[start : start + 30]):
                params.append((f"items[{index}][eventSessionId]", str(item["id"])))
                if item.get("activitySessionId") is not None:
                    params.append(
                        (
                            f"items[{index}][activitySessionId]",
                            str(item["activitySessionId"]),
                        )
                    )
            payload = await self._json(
                client,
                "/api/event-sessions/activity-sessions/transcript-states",
                params=params,
            )
            for state in _data_items(payload):
                key = (
                    str(state.get("eventSessionId") or ""),
                    str(state.get("activitySessionId") or ""),
                )
                session = by_key.get(key) or by_key.get((key[0], ""))
                if session is not None:
                    states.append((session, state))
        return states

    async def _download_transcript(
        self,
        client: httpx.AsyncClient,
        transcript_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        details_payload = await self._json(
            client, f"/api/transcript/{transcript_id}/details"
        )
        transcript_payload = await self._json(
            client,
            f"/api/transcript/{transcript_id}",
            # The web client uses 10,000; the gateway rejects 100,000 with HTTP 400.
            params={"perPage": 10_000},
        )
        details = (
            details_payload.get("data", {}) if isinstance(details_payload, dict) else {}
        )
        if not isinstance(details, dict):
            details = {}
        return details, _data_items(transcript_payload)

    async def _persist(
        self,
        session: AsyncSession,
        session_data: dict[str, Any],
        state: dict[str, Any],
        details: dict[str, Any],
        utterances: list[dict[str, Any]],
    ) -> bool:
        transcript_id = str(state.get("transcriptId") or "").strip()
        external_id = f"transcript:{transcript_id}"
        if await session.scalar(
            select(CommunicationEvent.id).where(
                CommunicationEvent.source_id == self.source.id,
                CommunicationEvent.external_id == external_id,
            )
        ):
            return False
        body = _format_transcript(utterances)
        if not body:
            return False
        interval = _meeting_interval(session_data)
        if interval is None:
            log.info(
                "mts_link_session_interval_not_ready",
                source_id=self.source.id,
                transcript_id=transcript_id,
            )
            return False
        starts_at, ends_at = interval
        entry = session_data.get("_entry", {})
        meeting_url = find_mts_link_url_in_payload(entry) or find_mts_link_url_in_payload(
            session_data
        )
        event_session_id = str(session_data.get("id") or state.get("eventSessionId") or "")
        activity_session_id = str(
            session_data.get("activitySessionId") or state.get("activitySessionId") or ""
        )
        event_id = str(session_data.get("eventId") or "")
        link_keys = mts_link_reference_keys(
            meeting_url,
            known_ids=(event_session_id, activity_session_id, event_id),
        )
        title = str(
            details.get("transcriptName")
            or session_data.get("name")
            or "Результаты встречи"
        ).strip()[:500]
        owner_name = str(details.get("ownerName") or "").strip() or None
        created_at = _parse_datetime(details.get("createdAt"), ends_at)
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        event = CommunicationEvent(
            source_id=self.source.id,
            source_type="mts_link",
            external_id=external_id,
            event_type=MEETING_TRANSCRIPT_EVENT_TYPE,
            direction=Direction.INCOMING,
            thread_external_id=f"mts-link:{event_session_id or transcript_id}",
            subject=title,
            author=owner_name,
            participants=_participants(utterances),
            occurred_at=created_at,
            body=body,
            source_url=meeting_url,
            raw_headers={
                "MTS-Link": {
                    "transcript_id": transcript_id,
                    "event_session_id": event_session_id,
                    "activity_session_id": activity_session_id or None,
                    "event_id": event_id or None,
                    "status": str(state.get("status") or ""),
                    "visibility": str(state.get("visibility") or ""),
                    "is_published": bool(state.get("isPublished")),
                    "structured_utterances_only": True,
                }
            },
            content_hash=content_hash,
        )
        session.add(event)
        await session.flush()
        meeting_result = MeetingResult(
            source_id=self.source.id,
            transcript_id=transcript_id,
            event_session_id=event_session_id,
            activity_session_id=activity_session_id or None,
            source_event_id=event.id,
            origin_type=MTS_TRANSCRIPT_ORIGIN,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            owner_name=owner_name,
            meeting_url=meeting_url,
            mts_link_keys=link_keys,
            transcript_status=str(state.get("status") or "ready")[:64],
        )
        session.add(meeting_result)
        await session.flush()
        await link_result_to_calendar(session, meeting_result)
        await attach_email_results_to_transcript(session, meeting_result)
        return True

    async def sync(self, session: AsyncSession) -> int:
        inserted = 0
        async with self._client() as client:
            sessions = await self._sessions(client)
            for session_data, state in await self._states(client, sessions):
                transcript_id = str(state.get("transcriptId") or "").strip()
                if not transcript_id or bool(state.get("isDisabled")):
                    continue
                external_id = f"transcript:{transcript_id}"
                if await session.scalar(
                    select(CommunicationEvent.id).where(
                        CommunicationEvent.source_id == self.source.id,
                        CommunicationEvent.external_id == external_id,
                    )
                ):
                    continue
                try:
                    details, utterances = await self._download_transcript(client, transcript_id)
                except MtsLinkApiError as exc:
                    log.warning(
                        "mts_link_transcript_not_ready",
                        source_id=self.source.id,
                        transcript_id=transcript_id,
                        status_code=exc.status_code,
                    )
                    continue
                if await self._persist(
                    session, session_data, state, details, utterances
                ):
                    inserted += 1
                    await session.commit()
        return inserted

    async def test_connection(self) -> dict[str, str]:
        async with self._client() as client:
            payload = await self._json(client, "/api/login")
        if not isinstance(payload, dict):
            raise RuntimeError("MTS Link profile response has an unexpected format")
        return {"status": "ok", "detail": "MTS Link access token is accepted"}
