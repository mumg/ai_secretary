"""Bounded evidence and payloads shared by archive retrieval and Ollama."""

from __future__ import annotations

import json
import re


class ChatContextError(ValueError):
    """The request cannot fit; retrying the same question will not resolve it."""


def excerpt(value: str | None, terms: list[str], limit: int = 1200) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    folded = text.casefold()
    # Several windows preserve evidence when query terms occur far apart.
    positions = sorted({folded.find(term) for term in terms if term in folded})
    if not positions:
        return text[: limit - 1] + "…"
    windows: list[tuple[int, int]] = []
    width = max(80, (limit - 12) // min(len(positions), 3))
    for position in positions:
        start = max(0, position - width // 3)
        end = min(len(text), start + width)
        if windows and start <= windows[-1][1]:
            continue
        windows.append((start, end))
        if len(windows) == 3:
            break
    return " … ".join(text[start:end] for start, end in windows)[:limit]


def serialized_size(value: object) -> int:
    # A conservative byte budget avoids a model-specific tokenizer dependency.
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def bounded_history(history: list[dict[str, str]], budget: int) -> list[dict[str, str]]:
    result = []
    for message in reversed(history[-12:]):
        # Source labels are local to each answer, not stable document identities.
        content = re.sub(r"\[[TE]\d+\]", "", message["content"])
        item = {"role": message["role"], "content": content}
        if serialized_size([item, *result]) > budget:
            continue
        result.insert(0, item)
    return result


def fit_records(
    records: list[dict[str, object]], budget: int, query: str = ""
) -> list[dict[str, object]]:
    """Share space fairly; a large early record must not hide smaller later ones."""
    if not records or budget <= 2:
        return []
    terms = re.findall(r"[\w@.-]{2,}", query.casefold())
    result: list[dict[str, object]] = []
    for index, original in enumerate(records):
        remaining = budget - serialized_size(result) - 2
        quota = max(240, remaining // (len(records) - index))
        item = dict(original)
        # Preserve identity, dates and author; trim evidence proportionally.
        text_fields = [
            key
            for key in ("content", "summary", "evidence", "title")
            if isinstance(item.get(key), str)
        ]
        while serialized_size(item) > quota and any(len(item[key]) > 120 for key in text_fields):
            key = max(text_fields, key=lambda key: len(item[key]))
            item[key] = excerpt(item[key], terms, max(100, len(item[key]) * 3 // 4))
        if serialized_size([*result, item]) <= budget:
            result.append(item)
    return result
