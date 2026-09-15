from __future__ import annotations

from collections.abc import Mapping
from typing import Any

IMPORTANCE_HEADERS = (
    "Importance",
    "Priority",
    "X-Priority",
    "X-MSMail-Priority",
)


def email_has_high_importance(headers: Mapping[str, Any] | None) -> bool:
    """Recognize the standard MIME forms used by Outlook's high-importance flag."""
    normalized = {
        str(name).casefold(): str(value or "").strip().casefold()
        for name, value in (headers or {}).items()
    }
    importance = normalized.get("importance", "")
    priority = normalized.get("priority", "")
    outlook_priority = normalized.get("x-msmail-priority", "")
    x_priority = normalized.get("x-priority", "")
    return (
        importance == "high"
        or priority in {"urgent", "high"}
        or outlook_priority == "high"
        or x_priority.startswith(("1", "2"))
    )
