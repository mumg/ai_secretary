from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from improver.enums import ComponentHealthStatus, Direction, TaskPriority, TaskStatus


class ReminderCreate(BaseModel):
    remind_at: datetime


class ReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    remind_at: datetime
    sent_at: datetime | None
    enabled: bool


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return " ".join(value.split())


class DictatedTaskCreate(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.split())


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    status: TaskStatus | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_at: datetime | None
    source_event_id: uuid.UUID | None
    evidence: str | None
    confidence: float | None
    ranking_score: float
    ranking_reasons: list[str]
    manually_created: bool
    completed_at: datetime | None
    reminders: list[ReminderRead]
    created_at: datetime
    updated_at: datetime


class TaskSourceRead(BaseModel):
    id: uuid.UUID
    source_id: str
    source_label: str
    source_type: str
    event_type: str
    direction: str
    subject: str | None
    author: str | None
    participants: list[dict[str, Any]]
    occurred_at: datetime
    body: str
    source_url: str | None


class TaskDetailRead(BaseModel):
    task: TaskRead
    source: TaskSourceRead | None


class PlanItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    pinned: bool
    automatically_added: bool
    task: TaskRead


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: str
    source_label: str | None = None
    source_event_id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    all_day: bool
    location: str | None
    organizer: dict[str, Any] | None
    attendees: list[dict[str, Any]]
    status: str
    method: str
    mts_link_url: str | None = None


class MeetingPage(BaseModel):
    items: list[MeetingRead]
    offset: int
    limit: int
    has_more: bool


class MeetingResultRead(BaseModel):
    id: uuid.UUID
    source_id: str
    source_label: str
    source_event_id: uuid.UUID
    calendar_meeting_id: uuid.UUID | None
    title: str
    starts_at: datetime
    ends_at: datetime
    owner_name: str | None
    meeting_url: str | None
    transcript_status: str
    summary: str | None
    decisions: list[str]
    agreements: list[str]
    analysis_state: str
    analyzed_at: datetime | None
    origin_type: str
    supplement_count: int = 0
    brief_summary: str
    time_known: bool = True
    received_at: datetime | None = None


class ParticipantMeetingSummary(BaseModel):
    source_event_id: uuid.UUID
    source_label: str
    author: str | None
    occurred_at: datetime
    summary: str
    decisions: list[str]
    agreements: list[str]


class MeetingResultDetail(MeetingResultRead):
    participants: list[dict[str, Any]] = Field(default_factory=list)
    participant_summaries: list[ParticipantMeetingSummary] = Field(default_factory=list)


class MeetingResultPage(BaseModel):
    items: list[MeetingResultRead]
    offset: int
    limit: int
    has_more: bool


class DailyPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_date: date
    generated_at: datetime
    items: list[PlanItemRead]
    meetings: list[MeetingRead] = Field(default_factory=list)


class DeviceUpsert(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    fcm_token: str = Field(min_length=10)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    active: bool
    last_seen_at: datetime | None


class EventIngest(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=32)
    external_id: str = Field(min_length=1, max_length=512)
    event_type: str = "message"
    direction: Direction = Direction.INCOMING
    thread_external_id: str | None = None
    subject: str | None = None
    author: str | None = None
    participants: list[dict[str, str]] = Field(default_factory=list)
    occurred_at: datetime
    body: str
    source_url: str | None = None


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: str
    source_type: str
    external_id: str
    event_type: str
    direction: str
    thread_external_id: str | None
    subject: str | None
    author: str | None
    participants: list[dict[str, str]]
    occurred_at: datetime
    body: str
    source_url: str | None
    analysis_state: str


class ConversationThreadRead(BaseModel):
    id: uuid.UUID
    source_id: str
    source_label: str
    source_type: str
    title: str | None
    participants: list[dict[str, Any]]
    summary: str | None
    event_count: int
    first_event_at: datetime
    last_event_at: datetime
    summarized_at: datetime | None


class ConversationThreadPage(BaseModel):
    items: list[ConversationThreadRead]
    offset: int
    limit: int
    has_more: bool


class ConversationEventRead(BaseModel):
    id: uuid.UUID
    event_type: str
    direction: str
    subject: str | None
    author: str | None
    occurred_at: datetime
    preview: str
    source_url: str | None


class ConversationThreadDetail(ConversationThreadRead):
    events: list[ConversationEventRead] = Field(default_factory=list)
    has_more_events: bool = False
    events_offset: int = 0
    events_limit: int = 100


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8_000)


class ChatQuery(BaseModel):
    query: str = Field(min_length=1, max_length=8_000)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=20)
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        return " ".join(value.split())


class ChatReference(BaseModel):
    key: str
    kind: Literal["task", "event"]
    id: uuid.UUID
    title: str
    source_label: str | None
    occurred_at: datetime | None
    snippet: str
    source_url: str | None


class ChatResponse(BaseModel):
    answer: str
    references: list[ChatReference]


class MeetingContextReference(ChatReference):
    meeting_result_id: uuid.UUID | None = None


class MeetingContextRead(BaseModel):
    meeting: MeetingRead
    status: str
    summary: str | None = None
    references: list[MeetingContextReference] = Field(default_factory=list)
    generated_at: datetime | None = None
    error: str | None = None
    stale: bool = False


class ChatRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query: str
    status: str
    answer: str | None
    references: list[ChatReference]
    error: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AdminSettingsWrite(BaseModel):
    settings: dict[str, Any]
    firebase_credentials_json: str | None = None


class AdminSettingsRead(BaseModel):
    settings: dict[str, Any]
    firebase_configured: bool
    filter_reconciliation: dict[str, int] | None = None
    identity_requeued: int | None = None


class TagReference(BaseModel):
    id: uuid.UUID
    name: str


class TagWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class TagRead(TagReference):
    source_count: int
    created_at: datetime
    updated_at: datetime


class SourceWrite(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=255)
    source_type: Literal["imap", "exchange", "mts_link", "external_tasks"]
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)
    credential: str | None = None
    tag_ids: list[uuid.UUID] | None = None


class SourceRead(BaseModel):
    id: str
    label: str
    source_type: str
    enabled: bool
    settings: dict[str, Any]
    tags: list[TagReference]
    credential_configured: bool
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ExternalTaskSourceWrite(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    @field_validator("label", mode="before")
    @classmethod
    def clean_label(cls, value: str) -> str:
        return " ".join(value.split())


class ExternalTaskSourceRead(BaseModel):
    id: str
    label: str
    enabled: bool
    tags: list[TagReference]
    created_at: datetime
    updated_at: datetime


class ExternalTaskWrite(BaseModel):
    external_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    status: TaskStatus = TaskStatus.NEW
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=4_000)
    evidence: str | None = Field(default=None, max_length=2_000)
    occurred_at: datetime | None = None
    source_updated_at: datetime | None = None

    @field_validator("external_id", "title", mode="before")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("due_at", "occurred_at", "source_updated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime values must include a timezone")
        return value


class ExternalTaskBatchWrite(BaseModel):
    tasks: list[ExternalTaskWrite] = Field(default_factory=list, max_length=500)
    close_missing: bool = False

    @field_validator("tasks")
    @classmethod
    def unique_external_ids(
        cls, tasks: list[ExternalTaskWrite]
    ) -> list[ExternalTaskWrite]:
        ids = [task.external_id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("external_id values must be unique within a batch")
        return tasks


class ExternalTaskSyncItem(BaseModel):
    external_id: str
    task_id: uuid.UUID
    action: Literal["created", "updated", "unchanged"]
    status: TaskStatus
    active: bool


class ExternalTaskBatchRead(BaseModel):
    source_id: str
    created: int
    updated: int
    unchanged: int
    closed_missing: int
    items: list[ExternalTaskSyncItem]


class ComponentStatusWrite(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    component_type: Literal["external", "external_loader", "integration"] = "external"
    status: Literal["OK", "BUSY", "DEGRADED", "ERROR", "UNKNOWN"]
    message: str | None = Field(default=None, max_length=2_000)
    metrics: dict[str, float] = Field(default_factory=dict, max_length=100)
    observed_at: datetime | None = None
    ttl_seconds: int | None = Field(default=300, ge=30, le=86_400)

    @field_validator("label", "component_type", mode="before")
    @classmethod
    def clean_component_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("message", mode="before")
    @classmethod
    def clean_component_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None

    @field_validator("observed_at")
    @classmethod
    def component_timestamp_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @field_validator("metrics", mode="before")
    @classmethod
    def metrics_are_numeric(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value.values()
        ):
            raise ValueError("component metrics must contain only numbers")
        return value


class ComponentStatusRead(BaseModel):
    id: str
    label: str
    component_type: str
    status: ComponentHealthStatus
    message: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    observed_at: datetime
    expires_at: datetime | None = None


class SystemStatusRead(BaseModel):
    overall_status: ComponentHealthStatus
    generated_at: datetime
    components: list[ComponentStatusRead]
