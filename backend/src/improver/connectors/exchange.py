from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from exchangelib import (
    BASIC,
    DELEGATE,
    DIGEST,
    NTLM,
    Account,
    Configuration,
    Credentials,
    EWSDateTime,
)
from exchangelib.transport import NOAUTH
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig, SourceConfig
from improver.connectors.base import SourceConnector
from improver.connectors.imap import ParsedAttachment, ParsedMessage, _parse_message
from improver.enums import AnalysisState, Direction
from improver.models import Attachment, CommunicationEvent, Meeting, SourceCursor
from improver.services.calendar_events import reinterpret_utc_calendar_as_local
from improver.services.mts_link import find_mts_link_urls

AUTH_TYPES = {
    "ntlm": NTLM,
    "basic": BASIC,
    "digest": DIGEST,
    "noauth": NOAUTH,
}

CALENDAR_CURSOR_KEY = "exchange_calendar_sync"
CALENDAR_REFRESH_INTERVAL = timedelta(minutes=15)
CALENDAR_PAST_WINDOW = timedelta(days=30)
CALENDAR_FUTURE_WINDOW = timedelta(days=365)
CALENDAR_VIEW_SLICE = timedelta(days=31)
CALENDAR_MAX_ITEMS = 500


@dataclass(frozen=True)
class ParsedCalendarItem:
    external_id: str
    occurrence_uid: str
    series_uid: str
    thread_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    all_day: bool
    location: str | None
    organizer: dict[str, str] | None
    attendees: list[dict[str, str]]
    status: str
    method: str
    body: str
    source_url: str | None
    occurred_at: datetime
    recurring: bool


class ExchangeConnector(SourceConnector):
    def __init__(self, source: SourceConfig, config: AppConfig):
        super().__init__(source, config)
        self.attachment_dir = config.server.data_dir / "attachments"

    def _account(self) -> Account:
        assert self.source.username
        assert self.source.ews_url
        assert self.source.primary_smtp_address
        credentials = Credentials(
            username=self.source.username,
            password=self.source.credential or "",
        )
        auth_type = AUTH_TYPES.get(self.source.auth_type.lower())
        if auth_type is None:
            raise ValueError(f"Unsupported Exchange auth_type: {self.source.auth_type}")
        configuration = Configuration(
            service_endpoint=self.source.ews_url,
            credentials=credentials,
            auth_type=auth_type,
        )
        return Account(
            primary_smtp_address=self.source.primary_smtp_address,
            config=configuration,
            autodiscover=False,
            access_type=DELEGATE,
        )

    @staticmethod
    def _as_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo:
                # EWSDateTime.astimezone() only accepts EWSTimeZone, so convert
                # through the Unix timestamp to return a regular UTC datetime.
                return datetime.fromtimestamp(value.timestamp(), UTC)
            return value.replace(tzinfo=UTC)
        return datetime.now(UTC)

    def _fetch_folder(
        self,
        folder_name: str,
        direction: Direction,
        since: datetime,
    ) -> list[ParsedMessage]:
        account = self._account()
        folder = account.inbox if folder_name == "inbox" else account.sent
        date_field = "datetime_received" if direction == Direction.INCOMING else "datetime_sent"
        ews_since = EWSDateTime.from_datetime(since)
        query = (
            folder.filter(**{f"{date_field}__gte": ews_since})
            .only("mime_content", "conversation_id", date_field)
            .order_by(date_field)
        )
        messages: list[ParsedMessage] = []
        # exchangelib 5.x QuerySet performs lazy, paged fetching from __iter__.
        # The old iterator(page_size=...) API no longer exists in 5.6.0.
        for index, item in enumerate(query, start=1):
            mime_content = bytes(item.mime_content or b"")
            if not mime_content:
                continue
            parsed = _parse_message(index, mime_content)
            calendar_event = parsed.headers.get("Calendar-Event")
            if isinstance(calendar_event, dict):
                parsed.headers["Calendar-Event"] = reinterpret_utc_calendar_as_local(
                    calendar_event,
                    self.config.server.timezone,
                )
            item_id = str(item.id)
            if not parsed.headers.get("Message-ID"):
                parsed.external_id = item_id
            conversation_id = getattr(item, "conversation_id", None)
            parsed.thread_id = str(
                getattr(conversation_id, "id", None) or parsed.thread_id or item_id
            )
            parsed.occurred_at = self._as_datetime(getattr(item, date_field, None))
            messages.append(parsed)
        return messages

    def _calendar_datetime(self, value: object) -> datetime:
        # CalendarView values are real timezone-aware EWS timestamps. The local
        # Exchange workaround used for malformed UTC values inside email ICS
        # attachments must not be applied here, or Moscow events shift by -3h.
        return self._as_datetime(value)

    @staticmethod
    def _mailbox(value: object) -> dict[str, str] | None:
        mailbox = getattr(value, "mailbox", None) or value
        name = str(getattr(mailbox, "name", None) or "").strip()
        address = str(
            getattr(mailbox, "email_address", None)
            or getattr(mailbox, "address", None)
            or ""
        ).strip()
        if not name and not address:
            return None
        return {"name": name, "address": address}

    @staticmethod
    def _occurrence_uid(series_uid: str, starts_at: datetime, recurring: bool) -> str:
        if not recurring:
            return series_uid[:512]
        identity = f"{series_uid}\0{starts_at.isoformat()}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        suffix = f"::{starts_at.isoformat()}::{digest}"
        return f"{series_uid[: 512 - len(suffix)]}{suffix}"

    def _is_self_only_meeting(
        self,
        organizer: dict[str, str] | None,
        attendees: list[dict[str, str]],
    ) -> bool:
        self_addresses = {
            value.strip().casefold()
            for value in (
                self.source.primary_smtp_address,
                self.source.username,
                *self.config.identity.addresses,
            )
            if value and "@" in value
        }
        organizer_address = (organizer or {}).get("address", "").strip().casefold()
        if not organizer_address or organizer_address not in self_addresses:
            return False
        attendee_addresses = {
            item.get("address", "").strip().casefold()
            for item in attendees
            if item.get("address", "").strip()
        }
        return not (attendee_addresses - self_addresses)

    def _fetch_calendar(
        self,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[ParsedCalendarItem]:
        account = self._account()
        raw_items: list[object] = []
        slice_start = starts_at
        while slice_start < ends_at:
            slice_end = min(slice_start + CALENDAR_VIEW_SLICE, ends_at)
            query = account.calendar.view(
                start=EWSDateTime.from_datetime(slice_start),
                end=EWSDateTime.from_datetime(slice_end),
                max_items=CALENDAR_MAX_ITEMS,
            ).only(
                "subject",
                "text_body",
                "body",
                "last_modified_time",
                "uid",
                "recurrence_id",
                "start",
                "end",
                "original_start",
                "is_all_day",
                "location",
                "is_cancelled",
                "is_recurring",
                "type",
                "organizer",
                "required_attendees",
                "optional_attendees",
                "resources",
                "meeting_workspace_url",
                "net_show_url",
            )
            raw_items.extend(query)
            slice_start = slice_end

        items_by_external_id: dict[str, ParsedCalendarItem] = {}
        for item in raw_items:
            starts = self._calendar_datetime(getattr(item, "start", None))
            ends = self._calendar_datetime(getattr(item, "end", None))
            series_uid = str(getattr(item, "uid", None) or getattr(item, "id", "")).strip()
            if not series_uid:
                continue
            item_type = str(getattr(item, "type", "single")).casefold()
            recurring = bool(
                getattr(item, "is_recurring", False)
                or getattr(item, "recurrence_id", None)
                or getattr(item, "original_start", None)
                or "occurrence" in item_type
                or "exception" in item_type
                or "recurring" in item_type
            )
            occurrence_identity = getattr(item, "original_start", None) or getattr(
                item, "recurrence_id", None
            )
            occurrence_start = (
                self._calendar_datetime(occurrence_identity)
                if occurrence_identity is not None
                else starts
            )
            occurrence_uid = self._occurrence_uid(
                series_uid, occurrence_start, recurring
            )
            organizer = self._mailbox(getattr(item, "organizer", None))
            attendees: list[dict[str, str]] = []
            for field in ("required_attendees", "optional_attendees", "resources"):
                for attendee in getattr(item, field, None) or []:
                    serialized = self._mailbox(attendee)
                    if serialized is not None:
                        attendees.append(serialized)
            if self._is_self_only_meeting(organizer, attendees):
                continue
            location_value = getattr(item, "location", None)
            location = str(
                getattr(location_value, "display_name", None) or location_value or ""
            ).strip()
            body = str(
                getattr(item, "text_body", None) or getattr(item, "body", None) or ""
            )
            explicit_urls = [
                str(value).strip()
                for value in (
                    getattr(item, "meeting_workspace_url", None),
                    getattr(item, "net_show_url", None),
                )
                if value
            ]
            discovered_urls = find_mts_link_urls(location, body, *explicit_urls)
            source_url = (discovered_urls or explicit_urls or [None])[0]
            cancelled = bool(getattr(item, "is_cancelled", False))
            last_modified = self._as_datetime(
                getattr(item, "last_modified_time", None)
            )
            external_id = "calendar:" + hashlib.sha256(
                occurrence_uid.encode("utf-8")
            ).hexdigest()
            items_by_external_id[external_id] = ParsedCalendarItem(
                    external_id=external_id,
                    occurrence_uid=occurrence_uid,
                    series_uid=series_uid,
                    thread_id="calendar-series:"
                    + hashlib.sha256(series_uid.encode("utf-8")).hexdigest(),
                    title=str(getattr(item, "subject", None) or "Встреча")[:500],
                    starts_at=starts,
                    ends_at=ends,
                    all_day=bool(getattr(item, "is_all_day", False)),
                    location=location[:1000] or None,
                    organizer=organizer,
                    attendees=attendees[:200],
                    status="CANCELLED" if cancelled else "CONFIRMED",
                    method="CANCEL" if cancelled else "REQUEST",
                    body=body,
                    source_url=source_url,
                    occurred_at=last_modified,
                    recurring=recurring,
            )
        return list(items_by_external_id.values())

    async def _cursor(self, session: AsyncSession, key: str) -> SourceCursor | None:
        return await session.scalar(
            select(SourceCursor).where(
                SourceCursor.source_id == self.source.id,
                SourceCursor.cursor_key == key,
            )
        )

    def _store_attachment(self, attachment: ParsedAttachment) -> tuple[Path, str]:
        digest = hashlib.sha256(attachment.payload).hexdigest()
        suffix = Path(attachment.filename).suffix.lower()[:16]
        self.attachment_dir.mkdir(parents=True, exist_ok=True)
        target = self.attachment_dir / f"{digest}{suffix}"
        if not target.exists():
            target.write_bytes(attachment.payload)
        return target, digest

    async def _sync_folder(
        self,
        session: AsyncSession,
        folder_name: str,
        direction: Direction,
    ) -> int:
        cursor_key = f"exchange_time:{folder_name}"
        cursor = await self._cursor(session, cursor_key)
        if cursor:
            since = datetime.fromisoformat(cursor.cursor_value) - timedelta(minutes=5)
        else:
            since = datetime.now(UTC) - timedelta(
                days=self.config.communication_sources.initial_sync_days
            )
        messages = await asyncio.to_thread(self._fetch_folder, folder_name, direction, since)
        inserted = 0
        max_seen = since
        for message in messages:
            max_seen = max(max_seen, message.occurred_at)
            existing = await session.scalar(
                select(CommunicationEvent).where(
                    CommunicationEvent.source_id == self.source.id,
                    CommunicationEvent.external_id == message.external_id,
                )
            )
            if existing:
                calendar_event = message.headers.get("Calendar-Event")
                if calendar_event and (
                    existing.event_type != message.event_type
                    or existing.raw_headers.get("Calendar-Event") != calendar_event
                ):
                    existing.event_type = message.event_type
                    existing.raw_headers = message.headers
                    existing.analysis_state = AnalysisState.PENDING
                    existing.analysis_error = None
                    inserted += 1
                continue
            content_hash = hashlib.sha256(
                "\0".join(
                    [
                        message.author or "",
                        message.occurred_at.isoformat(),
                        message.subject or "",
                        message.body,
                    ]
                ).encode("utf-8")
            ).hexdigest()
            event = CommunicationEvent(
                source_id=self.source.id,
                source_type="exchange",
                external_id=message.external_id,
                event_type=message.event_type,
                direction=direction,
                thread_external_id=message.thread_id,
                subject=message.subject,
                author=message.author,
                participants=message.participants,
                occurred_at=message.occurred_at,
                body=message.body,
                raw_headers=message.headers,
                content_hash=content_hash,
            )
            session.add(event)
            await session.flush()
            for parsed_attachment in message.attachments:
                if len(parsed_attachment.payload) > self.config.document_parser.max_bytes:
                    continue
                path, digest = await asyncio.to_thread(self._store_attachment, parsed_attachment)
                session.add(
                    Attachment(
                        event_id=event.id,
                        filename=parsed_attachment.filename,
                        media_type=parsed_attachment.media_type,
                        size_bytes=len(parsed_attachment.payload),
                        sha256=digest,
                        storage_path=str(path),
                    )
                )
            inserted += 1

        if cursor is None:
            session.add(
                SourceCursor(
                    source_id=self.source.id,
                    cursor_key=cursor_key,
                    cursor_value=max_seen.isoformat(),
                )
            )
        else:
            cursor.cursor_value = max_seen.isoformat()
        await session.commit()
        return inserted

    @staticmethod
    def _calendar_payload(item: ParsedCalendarItem) -> dict[str, Any]:
        return {
            "uid": item.occurrence_uid,
            "series_uid": item.series_uid,
            "title": item.title,
            "starts_at": item.starts_at.isoformat(),
            "ends_at": item.ends_at.isoformat(),
            "all_day": item.all_day,
            "location": item.location,
            "organizer": item.organizer,
            "attendees": item.attendees,
            "status": item.status,
            "method": item.method,
            "calendar_view": True,
            "recurring": item.recurring,
        }

    @staticmethod
    def _calendar_hash(item: ParsedCalendarItem, payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "payload": payload,
                    "body": item.body,
                    "source_url": item.source_url,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    async def _sync_calendar(self, session: AsyncSession, *, force: bool = False) -> int:
        cursor = await self._cursor(session, CALENDAR_CURSOR_KEY)
        now = datetime.now(UTC)
        if cursor is not None and not force:
            last_sync = datetime.fromisoformat(cursor.cursor_value)
            if now - last_sync < CALENDAR_REFRESH_INTERVAL:
                return 0

        window_start = now - CALENDAR_PAST_WINDOW
        window_end = now + CALENDAR_FUTURE_WINDOW
        items = await asyncio.to_thread(self._fetch_calendar, window_start, window_end)
        changed = 0
        seen_external_ids: set[str] = set()
        recurring_series: set[str] = set()
        for item in items:
            seen_external_ids.add(item.external_id)
            if item.recurring:
                recurring_series.add(item.series_uid)
            payload = self._calendar_payload(item)
            content_hash = self._calendar_hash(item, payload)
            existing = await session.scalar(
                select(CommunicationEvent).where(
                    CommunicationEvent.source_id == self.source.id,
                    CommunicationEvent.external_id == item.external_id,
                )
            )
            participants = ([item.organizer] if item.organizer else []) + item.attendees
            headers = {"Calendar-Event": payload, "Calendar-View": True}
            if existing is None:
                session.add(
                    CommunicationEvent(
                        source_id=self.source.id,
                        source_type="exchange",
                        external_id=item.external_id,
                        event_type="meeting_invitation",
                        direction=Direction.INTERNAL,
                        thread_external_id=item.thread_id,
                        subject=item.title,
                        author=(
                            (item.organizer or {}).get("address")
                            or (item.organizer or {}).get("name")
                        ),
                        participants=participants,
                        occurred_at=item.occurred_at,
                        body=item.body,
                        source_url=item.source_url,
                        raw_headers=headers,
                        content_hash=content_hash,
                    )
                )
                changed += 1
                continue
            if existing.content_hash == content_hash:
                continue
            existing.thread_external_id = item.thread_id
            existing.subject = item.title
            existing.author = (
                (item.organizer or {}).get("address")
                or (item.organizer or {}).get("name")
            )
            existing.participants = participants
            existing.occurred_at = item.occurred_at
            existing.body = item.body
            existing.source_url = item.source_url
            existing.raw_headers = headers
            existing.content_hash = content_hash
            existing.analysis_state = AnalysisState.PENDING
            existing.analysis_error = None
            existing.next_analysis_at = None
            changed += 1

        calendar_events = list(
            (
                await session.execute(
                    select(CommunicationEvent).where(
                        CommunicationEvent.source_id == self.source.id,
                        CommunicationEvent.event_type == "meeting_invitation",
                        CommunicationEvent.external_id.like("calendar:%"),
                    )
                )
            ).scalars()
        )
        for event in calendar_events:
            if event.external_id in seen_external_ids:
                continue
            payload = (event.raw_headers or {}).get("Calendar-Event")
            if not isinstance(payload, dict) or not payload.get("calendar_view"):
                continue
            try:
                event_start = datetime.fromisoformat(str(payload["starts_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not window_start <= event_start.astimezone(UTC) <= window_end:
                continue
            if payload.get("status") == "CANCELLED":
                continue
            cancelled_payload = dict(payload)
            cancelled_payload["status"] = "CANCELLED"
            cancelled_payload["method"] = "CANCEL"
            event.raw_headers = {
                **(event.raw_headers or {}),
                "Calendar-Event": cancelled_payload,
            }
            event.content_hash = hashlib.sha256(
                json.dumps(event.raw_headers, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            event.analysis_state = AnalysisState.PENDING
            event.analysis_error = None
            event.next_analysis_at = None
            changed += 1

        if recurring_series:
            masters = list(
                (
                    await session.execute(
                        select(Meeting).where(
                            Meeting.source_id == self.source.id,
                            Meeting.external_uid.in_(recurring_series),
                        )
                    )
                ).scalars()
            )
            for master in masters:
                master.status = "CANCELLED"

        if cursor is None:
            session.add(
                SourceCursor(
                    source_id=self.source.id,
                    cursor_key=CALENDAR_CURSOR_KEY,
                    cursor_value=now.isoformat(),
                )
            )
        else:
            cursor.cursor_value = now.isoformat()
        await session.commit()
        return changed

    async def sync(self, session: AsyncSession) -> int:
        incoming = await self._sync_folder(session, "inbox", Direction.INCOMING)
        outgoing = await self._sync_folder(session, "sent", Direction.OUTGOING)
        calendar = await self._sync_calendar(session)
        return incoming + outgoing + calendar

    async def test_connection(self) -> dict[str, str]:
        account = await asyncio.to_thread(self._account)
        await asyncio.to_thread(lambda: account.inbox.total_count)
        await asyncio.to_thread(lambda: account.calendar.total_count)
        return {"status": "ok", "detail": "Exchange inbox and calendar are available"}
