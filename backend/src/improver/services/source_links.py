"""Source-owned URL patterns, injected per operation rather than globally."""
from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache, wraps
from urllib.parse import urlsplit

import regex

MTS_LINK_DEFAULT_PATTERN = (
    r"^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d+)"
    r"(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$"
)


def default_link_patterns(source_type: str | None) -> list[str]:
    return [MTS_LINK_DEFAULT_PATTERN] if source_type == "mts_link" else []


@dataclass(frozen=True)
class LinkSource:
    source_id: str
    source_type: str
    patterns: tuple[str, ...]


_sources: ContextVar[tuple[LinkSource, ...]] = ContextVar("source_link_rules", default=())


@lru_cache(maxsize=256)
def compile_pattern(pattern: str):
    try:
        compiled = regex.compile(pattern)
    except regex.error as exc:
        raise ValueError(f"Некорректное правило ссылки: {exc}") from None
    if "meeting_id" not in compiled.groupindex:
        raise ValueError("В правиле ссылки нужна именованная группа (?P<meeting_id>...)")
    return compiled


def validate_patterns(patterns: list[str]) -> list[str]:
    if len(patterns) > 20:
        raise ValueError("Допустимо не более 20 правил ссылок на источник")
    for pattern in patterns:
        if not pattern or len(pattern) > 1024:
            raise ValueError("Правило ссылки должно содержать от 1 до 1024 символов")
        compile_pattern(pattern)
    return patterns


def parse_source_link(url: str, sources: tuple[LinkSource, ...] | None = None) -> list[dict[str, str]]:
    if len(url) > 4096:
        return []
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return []
    except ValueError:
        return []
    results = []
    for source in _sources.get() if sources is None else sources:
        for pattern in source.patterns:
            try:
                match = compile_pattern(pattern).fullmatch(url, timeout=0.01)
            except TimeoutError:
                # A misconfigured pattern must not stall event ingestion.
                continue
            if match is None:
                continue
            meeting_id = match.group("meeting_id")
            if not meeting_id or len(meeting_id) > 128 or not regex.fullmatch(r"[\w.-]+", meeting_id):
                continue
            results.append({"source_id": source.source_id, "source_type": source.source_type,
                            "meeting_id": meeting_id})
            break  # First matching rule wins within one source.
    return results


def inject_source_link_rules(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        sources = tuple(LinkSource(source.id, source.type, tuple(source.link_patterns))
                        for source in self.config.communication_sources.items if source.link_patterns)
        token = _sources.set(sources)
        try:
            return await method(self, *args, **kwargs)
        finally:
            _sources.reset(token)
    return wrapped


def source_references(*values: str | None) -> list[dict[str, str]]:
    if not _sources.get():
        return []
    references = []
    seen = set()
    for value in values:
        for url in re.findall(r"https?://[^\s<>\"']+", value or "")[:30]:
            for match in parse_source_link(url.rstrip(".,;:!?)]}>")):
                key = (match["source_id"], match["meeting_id"])
                if key not in seen:
                    seen.add(key)
                    references.append(match)
    return references
