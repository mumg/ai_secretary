from __future__ import annotations

import hashlib
import re

from sqlalchemy import func

from improver.models import CommunicationEvent
from improver.services.subject_identifiers import CLASSIFICATION_VERSION, classify_tokens

REPLY_PREFIX = re.compile(
    r"^(?:(?:re|fw|fwd|aw|wg|ответ|пересылка|пересл|переадресовано)"
    r"\s*(?:\[\d+\]|\(\d+\))?\s*:\s*)+",
    re.IGNORECASE,
)
STOP_WORDS = set("и в во на по к ко с со от для до о об из за the a an of to in on and for".split())


def subject_title(subject: str | None) -> str:
    return REPLY_PREFIX.sub("", " ".join((subject or "").split())).strip()


def subject_key(subject: str | None) -> str | None:
    normalized = subject_title(subject).casefold()
    return "subject:" + hashlib.sha256(normalized.encode()).hexdigest() if normalized else None


def subject_tokens(subject: str | None) -> list[str]:
    return sorted(
        {token.normalized for token in classify_tokens(subject_title(subject))} - STOP_WORDS
    )


def subject_classification(subject: str | None) -> dict:
    return {
        "version": CLASSIFICATION_VERSION,
        "subject_key": subject_key(subject),
        "tokens": [token.to_dict() for token in classify_tokens(subject_title(subject))],
    }


def subject_classification_for_llm(subject: str | None) -> dict:
    # Keep the prompt bounded even for unusually long subjects; words are in the subject itself.
    tokens = subject_classification(subject)["tokens"]
    return {
        "version": CLASSIFICATION_VERSION,
        "tokens": [
            {
                "token": token["token"][:128],
                "kind": token["kind"],
                "role": token["role"],
                "object_type": token["object_type"],
                "confidence": token["confidence"],
            }
            for token in tokens
            if token["kind"] != "word"
        ][:24],
    }


def email_thread_headers(subject: str | None, headers: dict, previous: dict | None = None) -> dict:
    result = dict(headers)
    for key in ("Original-Thread-Id", "Subject-Thread-Match"):
        if key in (previous or {}):
            result[key] = previous[key]
    result["Subject-Token-Classification"] = subject_classification(subject)
    return result


def comparison_tokens(classification: list[dict]) -> list[str]:
    result = set()
    for token in classification:
        word = token["normalized"]
        if word in STOP_WORDS:
            continue
        if (
            token["kind"] == "word"
            or (token["kind"] == "identifier" and token["role"] == "primary")
            or (token["kind"] == "unknown" and any(char.isalpha() for char in word))
        ):
            result.add(word)
    return sorted(result)


def subject_hit_rate(first: list[str], second: list[str]) -> float:
    left, right = set(first), set(second)

    if len(left & right) < 2:
        return 0.0
    return len(left & right) / len(left | right) if left | right else 0.0


def provider_thread_key(event: CommunicationEvent) -> str | None:
    return (event.raw_headers or {}).get("Original-Thread-Id") or event.thread_external_id


def provider_thread_expression(model=CommunicationEvent):
    return func.coalesce(
        model.raw_headers["Original-Thread-Id"].as_string(), model.thread_external_id
    )
