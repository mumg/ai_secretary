from __future__ import annotations

import re

REPLY_BOUNDARY = re.compile(
    r"^(?:On .+ wrote:|От:\s.+|From:\s.+|[-_]{2,}\s*(?:Original Message|Исходное сообщение))$",
    re.IGNORECASE,
)


def clean_email_body(value: str) -> str:
    """Keep the newest human-written part and remove common reply/signature blocks."""
    lines: list[str] = []
    for raw_line in value.replace("\x00", "").splitlines():
        line = raw_line.rstrip()
        if line.strip() == "--" or REPLY_BOUNDARY.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def bounded_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[текст сокращён сервером]"
