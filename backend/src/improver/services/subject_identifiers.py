"""Conservative, explainable classification of subject tokens; no model calls."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

CLASSIFICATION_VERSION = 1
TOKEN = re.compile(r"[^\W_]+(?:[.:/_-][^\W_]+)*", re.UNICODE)
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
KNOWN_IDS = {
    "inc": (re.compile(r"INC\d{6,}", re.I), "incident"),
    "req": (re.compile(r"REQ\d{6,}", re.I), "request"),
    "chg": (re.compile(r"CHG\d{6,}", re.I), "change"),
    "ritm": (re.compile(r"RITM\d{6,}", re.I), "request_item"),
    "bi": (re.compile(r"BI_\d+", re.I), "product"),
    "cpb": (re.compile(r"CPB\.\d+", re.I), "capability"),
}
OBJECT_CUE = re.compile(
    r"\b(задач[а-я]*|заявк[а-я]*|инцидент[а-я]*|договор[а-я]*|контракт[а-я]*|"
    r"документ[а-я]*|продукт[а-я]*|task|ticket|request|incident|contract|document|product)"
    r"\s*(?:№|#|номер|number|id)?\s*[:=]?\s*$",
    re.I,
)
RELATED_CUE = re.compile(
    r"(?:\bдополнени[а-я]*\s+к|\bссылк[а-я]*\s+на|\bсвязан[а-я]*\s+с|"
    r"\bв\s+рамках|\bсм\.?|\brelated\s+to|\bsee)\s*"
    r"(?:(?:задач[а-я]*|заявк[а-я]*|инцидент[а-я]*|договор[а-я]*)\s*)?"
    r"(?:№|#)?\s*$",
    re.I,
)
VERSION_CUE = re.compile(r"\b(?:верси[а-я]*|релиз[а-я]*|version|release)\s*[:№#]?\s*$", re.I)
QUANTITY_CUE = re.compile(r"\b(?:этап|шаг|пункт|часть|phase|step)\s*[:№#]?\s*$", re.I)
QUANTITY_SUFFIX = re.compile(
    r"\s*(?:%|шт\b|штук|вопрос[а-я]*\b|дн[яей]+\b|день\b|час[а-я]*\b|"
    r"минут[а-я]*\b|руб\b|процент[а-я]*\b|участник[а-я]*\b)",
    re.I,
)
MONTH = re.compile(
    r"(?:январ[а-я]*|феврал[а-я]*|март[а-я]*|апрел[а-я]*|ма[йяе]|июн[а-я]*|"
    r"июл[а-я]*|август[а-я]*|сентябр[а-я]*|октябр[а-я]*|ноябр[а-я]*|декабр[а-я]*)",
    re.I,
)


@dataclass(frozen=True)
class ClassifiedToken:
    token: str
    normalized: str
    kind: str
    object_type: str | None = None
    namespace: str | None = None
    role: str | None = None
    confidence: float = 1.0
    reason: str = "word"

    def to_dict(self) -> dict:
        return asdict(self)


def _object_type(cue: str) -> str:
    for prefix, result in (
        ("задач", "task"),
        ("task", "task"),
        ("ticket", "task"),
        ("заявк", "request"),
        ("request", "request"),
        ("инцидент", "incident"),
        ("incident", "incident"),
        ("договор", "contract"),
        ("контракт", "contract"),
        ("contract", "contract"),
        ("документ", "document"),
        ("document", "document"),
        ("продукт", "product"),
        ("product", "product"),
    ):
        if cue.casefold().startswith(prefix):
            return result
    return "object"


def _is_date(value: str) -> bool:
    match = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", value)
    if match:
        parts = [int(x) for x in match.groups()]
    else:
        match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", value)
        if not match:
            return False
        parts = [int(x) for x in reversed(match.groups())]
    try:
        date(*parts)
        return True
    except ValueError:
        return False


def classify_tokens(title: str) -> list[ClassifiedToken]:
    result = []
    for match in TOKEN.finditer(title):
        value = match.group()
        normalized = value.casefold()
        before, after = title[max(0, match.start() - 100) : match.start()], title[match.end() :]
        cue = OBJECT_CUE.search(before)
        related = bool(RELATED_CUE.search(before))
        role = "related" if related else "primary"
        known = next(
            (
                (namespace, kind)
                for namespace, (pattern, kind) in KNOWN_IDS.items()
                if pattern.fullmatch(value)
            ),
            None,
        )
        if known:
            namespace, object_type = known
            item = ClassifiedToken(
                value, normalized, "identifier", object_type, namespace, role, 0.99, "known_format"
            )
        elif UUID.fullmatch(value):
            item = ClassifiedToken(
                value,
                normalized,
                "identifier",
                _object_type(cue[1]) if cue else "object",
                "uuid",
                role,
                0.99,
                "uuid_format",
            )
        elif (
            cue
            and re.search(r"(?:№|#|\b(?:id|номер|number)\b)", cue[0], re.I)
            and re.fullmatch(r"\d+(?:[./-]\d+)*", value)
        ):
            item = ClassifiedToken(
                value,
                normalized,
                "identifier",
                _object_type(cue[1]),
                "number",
                role,
                0.99,
                "explicit_object_number",
            )
        elif _is_date(value):
            item = ClassifiedToken(value, normalized, "date", reason="calendar_date")
        elif re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", value):
            item = ClassifiedToken(value, normalized, "time", reason="clock_time")
        elif (
            re.fullmatch(r"v\d+(?:\.\d+)+(?:[-+][\w.]+)?", normalized)
            or (VERSION_CUE.search(before) and re.fullmatch(r"\d+(?:\.\d+)*", value))
            or re.fullmatch(r"\d+\.\d+\.\d+", value)
        ):
            item = ClassifiedToken(value, normalized, "version", reason="version_format_or_cue")
        elif cue and re.fullmatch(r"\d+(?:[/-]\d+)*", value):
            object_type = _object_type(cue[1])
            item = ClassifiedToken(
                value, normalized, "identifier", object_type, "number", role, 0.95, "object_label"
            )
        elif related and value.isdigit():
            item = ClassifiedToken(
                value,
                normalized,
                "identifier",
                "object",
                "number",
                "related",
                0.9,
                "related_object_label",
            )
        elif MONTH.fullmatch(value) or (
            re.fullmatch(r"(?:19|20)\d{2}", value)
            and (
                re.search(MONTH.pattern + r"\s+$", before, re.I)
                or re.match(r"\s*(?:год[а-я]*\b|г\.(?:\s|$))", after, re.I)
            )
        ):
            item = ClassifiedToken(value, normalized, "period", reason="calendar_period")
        elif re.fullmatch(r"\d+(?:[.,]\d+)?", value) and (
            QUANTITY_CUE.search(before) or QUANTITY_SUFFIX.match(after)
        ):
            item = ClassifiedToken(value, normalized, "quantity", reason="quantity_context")
        elif any(char.isdigit() for char in value):
            item = ClassifiedToken(
                value,
                normalized,
                "unknown",
                confidence=0.5,
                reason="unresolved_numeric_or_mixed_token",
            )
        else:
            item = ClassifiedToken(value, normalized, "word")
        result.append(item)
    return result


def primary_identifiers(tokens: list[dict]) -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = {}
    for token in tokens:
        if (
            token["kind"] == "identifier"
            and token["role"] == "primary"
            and token["confidence"] >= 0.9
        ):
            key = (token["namespace"], token["object_type"])
            result.setdefault(key, set()).add(token["normalized"])
    return result


def identifiers_conflict(first: list[dict], second: list[dict]) -> bool:
    left, right = primary_identifiers(first), primary_identifiers(second)
    # Multiple main IDs are ambiguous: the model must examine their roles instead.
    return any(
        len(left[key]) == len(right[key]) == 1 and left[key] != right[key]
        for key in left.keys() & right.keys()
    )
