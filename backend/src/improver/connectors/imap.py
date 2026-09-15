from __future__ import annotations

import asyncio
import base64
import hashlib
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig, SourceConfig
from improver.connectors.base import SourceConnector
from improver.enums import AnalysisState, Direction
from improver.models import Attachment, CommunicationEvent, SourceCursor
from improver.services.calendar_events import calendar_event_from_message
from improver.services.email_importance import IMPORTANCE_HEADERS


@dataclass(slots=True)
class ParsedAttachment:
    filename: str
    media_type: str
    payload: bytes


@dataclass(slots=True)
class ParsedMessage:
    uid: int
    external_id: str
    thread_id: str | None
    subject: str | None
    author: str | None
    participants: list[dict[str, str]]
    occurred_at: datetime
    body: str
    headers: dict[str, object]
    event_type: str = "email"
    attachments: list[ParsedAttachment] = field(default_factory=list)


def _decode(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def _addresses(
    from_value: str | None,
    to_value: str | None,
    cc_value: str | None,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for role, value in (("from", from_value), ("to", to_value), ("cc", cc_value)):
        for name, address in getaddresses([value] if value else []):
            key = (role, address.casefold())
            if not address or key in seen:
                continue
            seen.add(key)
            result.append(
                {"name": _decode(name) or "", "address": address, "role": role}
            )
    return result


def _safe_filename(value: str | None, index: int, content_type: str) -> str:
    decoded = _decode(value) or f"attachment-{index}"
    basename = Path(decoded).name
    basename = re.sub(r"[^\w.() -]+", "_", basename, flags=re.UNICODE).strip(". ")
    if basename:
        return basename[:240]
    subtype = content_type.split("/", 1)[-1] if "/" in content_type else "bin"
    return f"attachment-{index}.{subtype}"


def _message_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        media_type = part.get_content_type()
        if media_type not in {"text/plain", "text/html"}:
            continue
        try:
            value = part.get_content()
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            value = payload.decode("utf-8", errors="replace")
        if media_type == "text/plain":
            plain_parts.append(str(value))
        else:
            html_parts.append(BeautifulSoup(str(value), "html.parser").get_text("\n"))
    body = "\n".join(plain_parts or html_parts)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _parse_message(uid: int, raw: bytes) -> ParsedMessage:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = (message.get("Message-ID") or "").strip()
    references = str(message.get("References") or "").split()
    in_reply_to = (message.get("In-Reply-To") or "").strip()
    thread_index = _root_thread_index((message.get("Thread-Index") or "").strip())
    thread_id = thread_index or (references[0] if references else in_reply_to or message_id or None)
    try:
        occurred_at = parsedate_to_datetime(message.get("Date"))
    except (TypeError, ValueError):
        occurred_at = datetime.now(UTC)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    attachments: list[ParsedAttachment] = []
    for index, part in enumerate(message.iter_attachments(), start=1):
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        attachments.append(
            ParsedAttachment(
                filename=_safe_filename(part.get_filename(), index, part.get_content_type()),
                media_type=part.get_content_type(),
                payload=payload,
            )
        )

    relevant_headers: dict[str, object] = {
        name: str(message.get(name) or "")
        for name in (
            "Auto-Submitted",
            "Message-ID",
            "In-Reply-To",
            "Importance",
            "References",
            "Thread-Index",
            "Priority",
            "Precedence",
            "To",
            "Cc",
            "From",
            "Date",
            "X-Autoreply",
            "X-Autorespond",
            "X-MSMail-Priority",
            "X-Priority",
        )
    }
    calendar_methods = {
        str(part.get_param("method") or "").strip().upper()
        for part in message.walk()
        if part.get_content_type() == "text/calendar"
    }
    calendar_methods.discard("")
    if calendar_methods:
        relevant_headers["Calendar-Method"] = ",".join(sorted(calendar_methods))
    calendar_event = calendar_event_from_message(message)
    # METHOD:REPLY is a response to an invitation, not a calendar meeting of its own.
    # Keep it as email so a substantive comment can be assessed by the LLM, while a
    # plain accepted/declined response is removed by the technical analysis filter.
    is_calendar_invitation = bool(
        calendar_methods.intersection({"REQUEST", "PUBLISH", "CANCEL"})
        or (calendar_event and "REPLY" not in calendar_methods)
    )
    if calendar_event:
        relevant_headers["Calendar-Event"] = calendar_event
    return ParsedMessage(
        uid=uid,
        external_id=message_id or f"uid:{uid}",
        thread_id=thread_id,
        subject=_decode(message.get("Subject")),
        author=_decode(message.get("From")),
        participants=_addresses(message.get("From"), message.get("To"), message.get("Cc")),
        occurred_at=occurred_at,
        body=_message_body(message),
        headers=relevant_headers,
        event_type="meeting_invitation" if is_calendar_invitation else "email",
        attachments=attachments,
    )


def _root_thread_index(value: str) -> str:
    if not value:
        return ""
    try:
        decoded = base64.b64decode(value + "===", validate=False)
    except (ValueError, TypeError):
        return value
    if len(decoded) < 22:
        return value
    return base64.b64encode(decoded[:22]).decode("ascii")


class ImapConnector(SourceConnector):
    def __init__(self, source: SourceConfig, config: AppConfig):
        super().__init__(source, config)
        self.attachment_dir = config.server.data_dir / "attachments"

    async def _cursor(self, session: AsyncSession, key: str) -> SourceCursor | None:
        return await session.scalar(
            select(SourceCursor).where(
                SourceCursor.source_id == self.source.id,
                SourceCursor.cursor_key == key,
            )
        )

    def _fetch_folder(self, folder: str, last_uid: int | None) -> list[ParsedMessage]:
        assert self.source.host and self.source.port and self.source.username
        password = self.source.credential or ""
        if self.source.tls:
            connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.source.host,
                self.source.port,
                ssl_context=ssl.create_default_context(),
                timeout=30,
            )
        else:
            connection = imaplib.IMAP4(self.source.host, self.source.port, timeout=30)
            connection.starttls(ssl_context=ssl.create_default_context())
        try:
            connection.login(self.source.username, password)
            status, _ = connection.select(f'"{folder}"', readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select IMAP folder {folder!r}")
            if last_uid is not None:
                criteria = f"UID {last_uid + 1}:*"
            else:
                since = datetime.now(UTC) - timedelta(
                    days=self.config.communication_sources.initial_sync_days
                )
                criteria = f'SINCE "{since:%d-%b-%Y}"'
            status, data = connection.uid("search", None, criteria)
            if status != "OK" or not data:
                return []
            parsed: list[ParsedMessage] = []
            for raw_uid in data[0].split():
                uid = int(raw_uid)
                if last_uid is not None and uid <= last_uid:
                    continue
                fetch_status, payload = connection.uid("fetch", raw_uid, "(RFC822)")
                if fetch_status != "OK":
                    continue
                raw_message = next(
                    (item[1] for item in payload if isinstance(item, tuple) and item[1]), None
                )
                if raw_message:
                    parsed.append(_parse_message(uid, raw_message))
            return parsed
        finally:
            try:
                connection.logout()
            except imaplib.IMAP4.error:
                pass

    def _store_attachment(self, attachment: ParsedAttachment) -> tuple[Path, str]:
        digest = hashlib.sha256(attachment.payload).hexdigest()
        suffix = Path(attachment.filename).suffix.lower()[:16]
        self.attachment_dir.mkdir(parents=True, exist_ok=True)
        target = self.attachment_dir / f"{digest}{suffix}"
        if not target.exists():
            target.write_bytes(attachment.payload)
        return target, digest

    def _test_connection_sync(self) -> None:
        assert self.source.host and self.source.port and self.source.username
        password = self.source.credential or ""
        if self.source.tls:
            connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.source.host,
                self.source.port,
                ssl_context=ssl.create_default_context(),
                timeout=30,
            )
        else:
            connection = imaplib.IMAP4(self.source.host, self.source.port, timeout=30)
            connection.starttls(ssl_context=ssl.create_default_context())
        try:
            connection.login(self.source.username, password)
            for folder in (self.source.inbox_folder, self.source.sent_folder):
                status, _ = connection.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    raise RuntimeError(f"Unable to select IMAP folder {folder!r}")
        finally:
            try:
                connection.logout()
            except imaplib.IMAP4.error:
                pass

    async def test_connection(self) -> dict[str, str]:
        await asyncio.to_thread(self._test_connection_sync)
        return {"status": "ok", "detail": "IMAP folders are available"}

    async def _sync_folder(
        self,
        session: AsyncSession,
        folder: str,
        direction: Direction,
    ) -> int:
        cursor_key = f"imap_uid:{folder}"
        cursor = await self._cursor(session, cursor_key)
        last_uid = int(cursor.cursor_value) if cursor else None
        messages = await asyncio.to_thread(self._fetch_folder, folder, last_uid)
        inserted = 0
        max_uid = last_uid or 0
        for message in messages:
            max_uid = max(max_uid, message.uid)
            existing = await session.scalar(
                select(CommunicationEvent).where(
                    CommunicationEvent.source_id == self.source.id,
                    CommunicationEvent.external_id == message.external_id,
                )
            )
            if existing:
                calendar_event = message.headers.get("Calendar-Event")
                calendar_changed = bool(
                    calendar_event
                    and (
                        existing.event_type != message.event_type
                        or existing.raw_headers.get("Calendar-Event") != calendar_event
                    )
                )
                importance_changed = any(
                    existing.raw_headers.get(name) != message.headers.get(name)
                    for name in IMPORTANCE_HEADERS
                )
                if calendar_changed or importance_changed:
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
                source_type="imap",
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
        if max_uid:
            if cursor is None:
                session.add(
                    SourceCursor(
                        source_id=self.source.id,
                        cursor_key=cursor_key,
                        cursor_value=str(max_uid),
                    )
                )
            else:
                cursor.cursor_value = str(max_uid)
        await session.commit()
        return inserted

    async def sync(self, session: AsyncSession) -> int:
        incoming = await self._sync_folder(session, self.source.inbox_folder, Direction.INCOMING)
        outgoing = await self._sync_folder(session, self.source.sent_folder, Direction.OUTGOING)
        return incoming + outgoing
