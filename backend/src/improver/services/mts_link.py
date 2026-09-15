from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

MTS_LINK_HOST_SUFFIX = "mts-link.ru"
MTS_LINK_URL_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*mts-link\.ru/[^\s<>\"']*",
    flags=re.IGNORECASE,
)


def _clean_url(value: str) -> str:
    return value.rstrip(".,;:!?)]}>")


def is_mts_link_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").casefold()
    except ValueError:
        return False
    return host == MTS_LINK_HOST_SUFFIX or host.endswith(f".{MTS_LINK_HOST_SUFFIX}")


def find_mts_link_urls(*values: str | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in MTS_LINK_URL_RE.finditer(value or ""):
            url = _clean_url(match.group(0))
            if not is_mts_link_url(url):
                continue
            key = url.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(url)
    return result


def find_mts_link_url_in_payload(value: object) -> str | None:
    if isinstance(value, str):
        urls = find_mts_link_urls(value)
        return urls[0] if urls else None
    if isinstance(value, dict):
        preferred = ("link", "url", "joinLink", "eventUrl", "roomUrl")
        for key in preferred:
            if key in value and (url := find_mts_link_url_in_payload(value[key])):
                return url
        for nested in value.values():
            if url := find_mts_link_url_in_payload(nested):
                return url
    if isinstance(value, list):
        for nested in value:
            if url := find_mts_link_url_in_payload(nested):
                return url
    return None


def mts_link_reference_keys(
    *values: str | None,
    known_ids: Iterable[str | int | None] = (),
) -> list[str]:
    keys: set[str] = set()
    for raw_id in known_ids:
        normalized_id = str(raw_id or "").strip().casefold()
        if normalized_id:
            keys.add(f"id:{normalized_id}")
    for url in find_mts_link_urls(*values):
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        path = re.sub(r"/+", "/", unquote(parsed.path)).rstrip("/") or "/"
        parts = [part for part in path.split("/") if part]
        # A URL can contain an owner/account ID before the meeting ID. Route
        # names and owner IDs must never merge otherwise unrelated meetings.
        meeting_path = (
            len(parts) == 1
            and parts[0].isdigit()
            or len(parts) in (2, 3)
            and parts[0].casefold() == "j"
            or len(parts) == 2
            and parts[0].isdigit()
            or len(parts) == 2
            and parts[0].casefold() in {"join", "meetings", "events", "room"}
            or len(parts) == 5
            and parts[0].casefold() == "j"
            and parts[3].casefold() == "session"
            and parts[2].isdigit()
            and parts[4].isdigit()
        )
        if meeting_path:
            canonical = urlunsplit((parsed.scheme.casefold(), host, path, "", ""))
            keys.add(f"url:{canonical.casefold()}")
            keys.add(f"id:{parts[-1].casefold()}")
        for name, query_value in parse_qsl(parsed.query, keep_blank_values=False):
            if (
                name.casefold()
                in {"eventid", "eventsessionid", "activitysessionid", "meetingid", "roomid"}
                and query_value.strip()
            ):
                keys.add(f"id:{query_value.strip().casefold()}")
    return sorted(keys)


def references_overlap(left: Iterable[str], right: Iterable[str]) -> bool:
    left_keys = set(left)
    right_keys = set(right)
    if not left_keys or not right_keys:
        return False
    if left_keys & right_keys:
        return True
    left_ids = {key for key in left_keys if key.startswith("id:")}
    right_ids = {key for key in right_keys if key.startswith("id:")}
    return bool(left_ids & right_ids)
