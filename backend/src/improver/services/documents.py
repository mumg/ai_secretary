from __future__ import annotations

from pathlib import Path

import httpx

from improver.config import AppConfig

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx"}


class DocumentParserClient:
    def __init__(self, config: AppConfig):
        self.config = config

    async def extract(self, path: Path, media_type: str) -> str:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported attachment type: {path.suffix}")
        if path.stat().st_size > self.config.document_parser.max_bytes:
            raise ValueError("Attachment exceeds configured size limit")
        timeout = httpx.Timeout(self.config.document_parser.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            with path.open("rb") as source:
                response = await client.post(
                    f"{self.config.document_parser.base_url.rstrip('/')}/extract",
                    files={"file": (path.name, source, media_type)},
                )
        response.raise_for_status()
        text = str(response.json().get("text", ""))
        return text[: self.config.document_parser.max_characters]
