from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta, timezone
from email.message import Message
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _unfold(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _property(line: str) -> tuple[str, dict[str, str], str] | None:
    quoted = False
    delimiter = -1
    for index, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            delimiter = index
            break
    if delimiter < 0:
        return None
    raw_name, value = line[:delimiter], line[delimiter + 1 :]
    parts = raw_name.split(";")
    params: dict[str, str] = {}
    for raw_param in parts[1:]:
        if "=" in raw_param:
            key, param_value = raw_param.split("=", 1)
            params[key.upper()] = param_value.strip('"')
    return parts[0].upper(), params, value


def _calendar_datetime(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and "T" not in value):
        parsed_date = datetime.strptime(value[:8], "%Y%m%d").date()
        return datetime.combine(parsed_date, time.min, UTC), True
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), False
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    timezone_name = params.get("TZID")
    if timezone_name:
        try:
            return parsed.replace(tzinfo=ZoneInfo(timezone_name)), False
        except ZoneInfoNotFoundError:
            if match := re.search(r"UTC\s*([+-])(\d{1,2}):(\d{2})", timezone_name):
                direction = 1 if match.group(1) == "+" else -1
                offset = direction * timedelta(
                    hours=int(match.group(2)), minutes=int(match.group(3))
                )
                return parsed.replace(tzinfo=timezone(offset)), False
    return parsed.replace(tzinfo=UTC), False


def _mailbox(value: str, params: dict[str, str]) -> dict[str, str]:
    address = value.removeprefix("mailto:").removeprefix("MAILTO:").strip()
    return {"name": _unescape(params.get("CN", "")), "address": address}


def parse_calendar_event(calendar_text: str, mime_method: str = "") -> dict[str, object] | None:
    properties: list[tuple[str, dict[str, str], str]] = []
    inside_event = False
    document_method = mime_method.upper()
    for line in _unfold(calendar_text):
        parsed = _property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "METHOD" and not inside_event:
            document_method = value.strip().upper()
        elif name == "BEGIN" and value.strip().upper() == "VEVENT":
            inside_event = True
        elif name == "END" and value.strip().upper() == "VEVENT":
            break
        elif inside_event:
            properties.append((name, params, value))

    if not properties or document_method == "REPLY":
        return None
    grouped: dict[str, list[tuple[dict[str, str], str]]] = {}
    for name, params, value in properties:
        grouped.setdefault(name, []).append((params, value))
    if "UID" not in grouped or "DTSTART" not in grouped:
        return None

    starts_at, all_day = _calendar_datetime(*reversed(grouped["DTSTART"][0]))
    if "DTEND" in grouped:
        ends_at, _ = _calendar_datetime(*reversed(grouped["DTEND"][0]))
    else:
        ends_at = starts_at + (timedelta(days=1) if all_day else timedelta(hours=1))

    def first(name: str) -> str:
        values = grouped.get(name, [])
        return _unescape(values[0][1]) if values else ""

    organizer = (
        _mailbox(grouped["ORGANIZER"][0][1], grouped["ORGANIZER"][0][0])
        if grouped.get("ORGANIZER")
        else None
    )
    attendees = [_mailbox(value, params) for params, value in grouped.get("ATTENDEE", [])]
    status = first("STATUS").upper() or (
        "CANCELLED" if document_method == "CANCEL" else "CONFIRMED"
    )
    return {
        "uid": first("UID")[:512],
        "title": (first("SUMMARY") or "Встреча")[:500],
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "all_day": all_day,
        "location": first("LOCATION")[:1000] or None,
        "organizer": organizer,
        "attendees": attendees[:200],
        "status": status[:32],
        "method": document_method[:32] or "REQUEST",
    }


def calendar_event_from_message(message: Message) -> dict[str, object] | None:
    for part in message.walk():
        if part.get_content_type() != "text/calendar":
            continue
        method = str(part.get_param("method") or "")
        try:
            content = str(part.get_content())
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", errors="replace")
        try:
            event = parse_calendar_event(content, method)
        except (KeyError, TypeError, ValueError):
            continue
        if event is not None:
            return event
    return None


def reinterpret_utc_calendar_as_local(
    event: dict[str, object], timezone_name: str
) -> dict[str, object]:
    """Work around local Exchange servers that label local calendar wall time as UTC."""
    corrected = dict(event)
    local_timezone = ZoneInfo(timezone_name)
    for field in ("starts_at", "ends_at"):
        parsed = datetime.fromisoformat(str(event[field]))
        if parsed.utcoffset() == timedelta(0):
            corrected[field] = parsed.replace(tzinfo=None).replace(
                tzinfo=local_timezone
            ).isoformat()
    return corrected
