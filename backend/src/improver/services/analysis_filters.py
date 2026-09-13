from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Literal

from improver.config import AnalysisFilterConfig
from improver.enums import AnalysisState
from improver.models import CommunicationEvent
from improver.services.text import bounded_text


@dataclass(frozen=True)
class AnalysisFilterMatch:
    kind: Literal["stop_word", "address", "meeting_response", "automatic_reply"]
    value: str

    @property
    def technical(self) -> bool:
        return self.kind in {"meeting_response", "automatic_reply"}


MEETING_RESPONSE_SUBJECT = re.compile(
    r"^\s*(?:accepted|declined|tentative|принято|отклонено|предварительно|под вопросом)\s*:",
    re.IGNORECASE,
)
AUTOMATIC_REPLY_SUBJECT = re.compile(
    r"^\s*(?:automatic\s+reply|auto[ -]?reply|out\s+of\s+office|ooo|"
    r"автоматический\s+ответ|автоответ|нет\s+на\s+рабочем\s+месте)\s*[:\-]",
    re.IGNORECASE,
)
MEETING_RESULT_CONTENT = re.compile(
    r"\b(?:по\s+итогам|итоги\s+встречи|результат(?:ы|ам)?\s+(?:встречи|совещания)|"
    r"протокол|договорил(?:ись|ся|ась)|решил(?:и|а)?|принят(?:о|ы)\s+решени|"
    r"meeting\s+(?:minutes|results)|follow[ -]?up|agreed|decided|action\s+items?)\b",
    re.IGNORECASE,
)


def _addresses(event: CommunicationEvent) -> set[str]:
    raw_values = [event.author or ""]
    for participant in event.participants:
        if isinstance(participant, dict):
            raw_values.extend(
                str(participant.get(key) or "") for key in ("address", "email")
            )
        else:
            raw_values.append(str(participant))
    # Parse every stored field independently. A malformed display name in one field
    # (for example, an unmatched parenthesis) must not invalidate otherwise valid
    # addresses from the sender or other participants.
    return {
        address.casefold()
        for raw_value in raw_values
        for _, address in getaddresses([raw_value])
        if address
    }


def _configured_address(value: str) -> str:
    parsed = getaddresses([value])
    if parsed and parsed[0][1]:
        return parsed[0][1].casefold()
    return value.strip().casefold()


def match_analysis_filter(
    event: CommunicationEvent,
    filters: AnalysisFilterConfig,
) -> AnalysisFilterMatch | None:
    subject = event.subject or ""
    headers = {
        str(name).casefold(): str(value).strip()
        for name, value in (event.raw_headers or {}).items()
    }
    auto_submitted = headers.get("auto-submitted", "").casefold()
    precedence = headers.get("precedence", "").casefold()
    if auto_submitted and auto_submitted != "no":
        return AnalysisFilterMatch(kind="automatic_reply", value="Auto-Submitted")
    if headers.get("x-autoreply") or headers.get("x-autorespond"):
        return AnalysisFilterMatch(kind="automatic_reply", value="X-Autoreply")
    if precedence in {"auto-reply", "auto_reply"}:
        return AnalysisFilterMatch(kind="automatic_reply", value="Precedence: auto-reply")
    if match := AUTOMATIC_REPLY_SUBJECT.match(subject):
        return AnalysisFilterMatch(kind="automatic_reply", value=match.group(0).strip())

    contains_result_content = bool(MEETING_RESULT_CONTENT.search(event.body or ""))
    calendar_method = headers.get("calendar-method", "").casefold()
    if (
        "reply" in {item.strip() for item in calendar_method.split(",")}
        and not contains_result_content
    ):
        return AnalysisFilterMatch(kind="meeting_response", value="Calendar-Method: REPLY")
    if (match := MEETING_RESPONSE_SUBJECT.match(subject)) and not contains_result_content:
        return AnalysisFilterMatch(kind="meeting_response", value=match.group(0).strip())

    searchable_text = "\n".join((event.subject or "", event.body or "")).casefold()
    for stop_word in filters.stop_words:
        if stop_word.casefold() in searchable_text:
            return AnalysisFilterMatch(kind="stop_word", value=stop_word)

    event_addresses = _addresses(event)
    for configured in filters.excluded_addresses:
        if _configured_address(configured) in event_addresses:
            return AnalysisFilterMatch(kind="address", value=configured)
    return None


def filtered_state(match: AnalysisFilterMatch) -> AnalysisState:
    return AnalysisState.IGNORED if match.technical else AnalysisState.SKIPPED


def apply_filtered_index(event: CommunicationEvent, match: AnalysisFilterMatch) -> None:
    event.semantic_summary = bounded_text(
        event.subject or event.body or "Сообщение без темы",
        8_000,
    )
    category = {
        "meeting_response": "Технический ответ календаря",
        "automatic_reply": "Автоматический ответ об отсутствии",
    }.get(match.kind, "Исключено из автоматического анализа")
    event.semantic_categories = [category]
    event.semantic_keywords = [match.value] if match.kind == "stop_word" else []
    event.semantic_people = []
    event.semantic_organizations = []
    event.semantic_decisions = []
    event.semantic_agreements = []
    participant_values = [
        str(participant.get("address") or participant.get("email") or participant.get("name") or "")
        for participant in event.participants
        if isinstance(participant, dict)
    ]
    event.semantic_index = "\n".join(
        value
        for value in [
            event.subject,
            event.author,
            *participant_values,
            event.body,
            event.semantic_summary,
            *event.semantic_categories,
            *event.semantic_keywords,
        ]
        if value
    ).casefold()
    event.semantic_version = 1


def reset_filtered_event(event: CommunicationEvent) -> None:
    event.analysis_state = AnalysisState.PENDING
    event.analysis_error = None
    event.analysis_model = None
    event.analysis_result = None
    event.analyzed_at = None
    event.semantic_summary = None
    event.semantic_categories = []
    event.semantic_keywords = []
    event.semantic_people = []
    event.semantic_organizations = []
    event.semantic_decisions = []
    event.semantic_agreements = []
    event.semantic_index = None
    event.semantic_version = 0
