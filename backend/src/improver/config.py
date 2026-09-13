from __future__ import annotations

import os
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.engine import make_url


def read_secret(path: str | Path | None, *, required: bool = True) -> str | None:
    if not path:
        if required:
            raise ValueError("Secret file path is required")
        return None
    secret_path = Path(path)
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise ValueError(f"Secret file does not exist: {secret_path}") from None
        return None
    if not value and required:
        raise ValueError(f"Secret file is empty: {secret_path}")
    return value or None


class ServerConfig(BaseModel):
    public_url: str = "https://localhost"
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"
    data_dir: Path = Path("/data")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("timezone must be a valid IANA timezone") from None
        return value

    @field_validator("public_url")
    @classmethod
    def secure_public_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("public_url must be an absolute HTTPS URL")
        return value.rstrip("/")


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://improver@db:5432/improver"
    password_file: str | None = "/run/secrets/postgres_password"

    def resolved_url(self) -> str:
        url = make_url(self.url)
        if url.password:
            return url.render_as_string(hide_password=False)
        password = read_secret(self.password_file)
        return url.set(password=password).render_as_string(hide_password=False)


class CalendarConfig(BaseModel):
    country: str = "RU"
    workday_start: str = "10:00"
    workday_end: str = "17:00"
    daily_plan_time: str = "08:00"
    auto_update: bool = True
    weekend_due_policy: Literal["previous_workday"] = "previous_workday"
    working_dates: list[str] = Field(default_factory=list)
    non_working_dates: list[str] = Field(default_factory=list)

    @field_validator("working_dates", "non_working_dates")
    @classmethod
    def valid_dates(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("calendar override dates must use YYYY-MM-DD format") from None
        return values

    @model_validator(mode="after")
    def valid_schedule(self) -> CalendarConfig:
        parsed: dict[str, time] = {}
        for field_name in ("workday_start", "workday_end", "daily_plan_time"):
            value = getattr(self, field_name)
            try:
                parsed[field_name] = time.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{field_name} must use HH:MM format") from None
            if len(value) != 5:
                raise ValueError(f"{field_name} must use HH:MM format")
        if parsed["workday_start"] >= parsed["workday_end"]:
            raise ValueError("workday_start must be earlier than workday_end")
        return self


class LlmConfig(BaseModel):
    base_url: str = "http://ollama:11434"
    model: str = "qwen3.8:27b-q4_K_M"
    context_length: int = Field(default=16_384, ge=4_096, le=131_072)
    temperature: float = Field(default=0.1, ge=0, le=2)
    auto_create_confidence: float = Field(default=0.85, ge=0, le=1)
    possible_completion_confidence: float = Field(default=0.80, ge=0, le=1)
    request_timeout_seconds: int = Field(default=300, ge=10, le=1800)

    @field_validator("base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LLM base_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")


class WorkerConfig(BaseModel):
    poll_interval_seconds: int = Field(default=60, ge=10)
    batch_size: int = Field(default=10, ge=1, le=100)
    ranking_interval_seconds: int = Field(default=900, ge=60)


class NotificationConfig(BaseModel):
    firebase_credentials: dict[str, Any] | None = Field(default=None, exclude=True)
    due_soon_minutes: int = Field(default=60, ge=1, le=10_080)
    overdue_repeat_hour: int = Field(default=10, ge=0, le=23)


class DocumentParserConfig(BaseModel):
    base_url: str = "http://document-parser:8080"
    timeout_seconds: int = Field(default=30, ge=5, le=300)
    max_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024 * 1024,
        le=25 * 1024 * 1024,
    )
    max_characters: int = Field(default=100_000, ge=1000, le=100_000)


class SourceConfig(BaseModel):
    id: str
    type: Literal["imap", "exchange", "mts_link"]
    enabled: bool = True
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65_535)
    tls: bool = True
    username: str | None = None
    credential: str | None = Field(default=None, exclude=True)
    inbox_folder: str = "INBOX"
    sent_folder: str = "Sent"
    ews_url: str | None = None
    primary_smtp_address: str | None = None
    auth_type: str = "ntlm"
    base_url: str | None = None
    content: list[str] = Field(default_factory=list)
    poll_interval_seconds: int | None = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> SourceConfig:
        if not self.enabled:
            return self
        has_credential = bool(self.credential)
        if self.type == "imap" and not (
            all((self.host, self.port, self.username)) and has_credential
        ):
            raise ValueError(f"IMAP source {self.id!r} lacks connection settings")
        if self.type == "exchange" and not all(
            (self.ews_url, self.primary_smtp_address, self.username, has_credential)
        ):
            raise ValueError(f"Exchange source {self.id!r} lacks connection settings")
        if self.type == "mts_link" and not (self.base_url and has_credential):
            raise ValueError(f"MTS Link source {self.id!r} lacks connection settings")
        return self

    @field_validator("base_url")
    @classmethod
    def valid_optional_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source base_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")


class CommunicationSourcesConfig(BaseModel):
    initial_sync_days: int = Field(default=30, ge=1, le=365)
    items: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> CommunicationSourcesConfig:
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("communication source IDs must be unique")
        return self


class IdentityConfig(BaseModel):
    addresses: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)


class AnalysisFilterConfig(BaseModel):
    stop_words: list[str] = Field(default_factory=list, max_length=500)
    excluded_addresses: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("stop_words", "excluded_addresses")
    @classmethod
    def clean_entries(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = " ".join(value.split()).strip()
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    document_parser: DocumentParserConfig = Field(default_factory=DocumentParserConfig)
    communication_sources: CommunicationSourcesConfig = Field(
        default_factory=CommunicationSourcesConfig
    )
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    analysis_filters: AnalysisFilterConfig = Field(default_factory=AnalysisFilterConfig)


def load_config() -> AppConfig:
    raw: dict[str, Any] = {}
    raw.setdefault("database", {})
    if database_url := os.getenv("DATABASE_URL"):
        raw["database"]["url"] = database_url
    if password_file := os.getenv("DATABASE_PASSWORD_FILE"):
        raw["database"]["password_file"] = password_file
    raw.setdefault("server", {})
    if data_dir := os.getenv("DATA_DIR"):
        raw["server"]["data_dir"] = data_dir
    if log_level := os.getenv("LOG_LEVEL"):
        raw["server"]["log_level"] = log_level
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
