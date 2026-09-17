from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from improver.enums import (
    AnalysisState,
    AttachmentState,
    ChatRequestStatus,
    Direction,
    PrioritySource,
    TaskPriority,
    TaskStatus,
)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DatabaseSchemaVersion(Base):
    __tablename__ = "database_schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CommunicationEvent(Base, TimestampMixin):
    __tablename__ = "communication_events"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_event_source_external"),
        Index("ix_events_analysis", "analysis_state", "occurred_at"),
        Index("ix_events_thread", "source_id", "thread_external_id"),
        Index("ix_events_subject", "source_id", "subject_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("communication_sources.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default=Direction.INCOMING, nullable=False)
    thread_external_id: Mapped[str | None] = mapped_column(String(512))
    subject: Mapped[str | None] = mapped_column(Text)
    subject_key: Mapped[str | None] = mapped_column(String(80))
    subject_tokens: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    author: Mapped[str | None] = mapped_column(String(512))
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis_state: Mapped[str] = mapped_column(
        String(32), default=AnalysisState.PENDING, nullable=False
    )
    analysis_error: Mapped[str | None] = mapped_column(Text)
    analysis_model: Mapped[str | None] = mapped_column(String(255))
    analysis_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    semantic_summary: Mapped[str | None] = mapped_column(Text)
    semantic_categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    semantic_keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    semantic_people: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    semantic_organizations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    semantic_decisions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    semantic_agreements: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    semantic_index: Mapped[str | None] = mapped_column(Text)
    semantic_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_mailing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mailing_confidence: Mapped[float | None] = mapped_column(Float)
    mailing_kind: Mapped[str | None] = mapped_column(String(32))
    mailing_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mailing_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="source_event", lazy="selectin")


class ConversationThread(Base, TimestampMixin):
    __tablename__ = "conversation_threads"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "thread_external_id",
            name="uq_conversation_thread_source_external",
        ),
        Index("ix_conversation_threads_recent", "last_event_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("communication_sources.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("communication_events.id", ondelete="SET NULL"), index=True
    )
    summary_model: Mapped[str | None] = mapped_column(String(255))
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communication_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_state: Mapped[str] = mapped_column(
        String(32), default=AttachmentState.PENDING, nullable=False
    )
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_error: Mapped[str | None] = mapped_column(Text)

    event: Mapped[CommunicationEvent] = relationship(back_populates="attachments")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_active_rank", "status", "ranking_score"),
        Index("ix_tasks_due", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.NEW, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default=TaskPriority.NORMAL, nullable=False)
    priority_source: Mapped[str] = mapped_column(
        String(16), default=PrioritySource.MANUAL, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("communication_events.id", ondelete="CASCADE"), index=True
    )
    evidence: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    ranking_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ranking_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    manually_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_notification_date: Mapped[date | None] = mapped_column(Date)

    source_event: Mapped[CommunicationEvent | None] = relationship(back_populates="tasks")
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_due", "sent_at", "remind_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    task: Mapped[Task] = relationship(back_populates="reminders")


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_uid", name="uq_meeting_source_uid"),
        Index("ix_meetings_time", "starts_at", "ends_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("communication_sources.id", ondelete="CASCADE"), nullable=False
    )
    external_uid: Mapped[str] = mapped_column(String(512), nullable=False)
    source_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communication_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    mts_link_url: Mapped[str | None] = mapped_column(Text)
    mts_link_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    organizer: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED", nullable=False)
    method: Mapped[str] = mapped_column(String(32), default="REQUEST", nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingContext(Base, TimestampMixin):
    __tablename__ = "meeting_contexts"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), default="NOT_REQUESTED", nullable=False)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    meeting_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[str | None] = mapped_column(Text)


class MeetingResult(Base, TimestampMixin):
    __tablename__ = "meeting_results"
    __table_args__ = (
        UniqueConstraint("source_id", "transcript_id", name="uq_meeting_result_source_transcript"),
        Index("ix_meeting_results_time", "starts_at", "id"),
        Index("ix_meeting_results_session", "event_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("communication_sources.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[str | None] = mapped_column(String(128))
    event_session_id: Mapped[str | None] = mapped_column(String(128))
    activity_session_id: Mapped[str | None] = mapped_column(String(128))
    source_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("communication_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    calendar_meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), index=True
    )
    parent_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting_results.id", ondelete="SET NULL"), index=True
    )
    origin_type: Mapped[str] = mapped_column(String(32), default="mts_transcript", nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner_name: Mapped[str | None] = mapped_column(String(500))
    meeting_url: Mapped[str | None] = mapped_column(Text)
    mts_link_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    transcript_status: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    agreements: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyPlan(Base, TimestampMixin):
    __tablename__ = "daily_plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    items: Mapped[list[DailyPlanItem]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DailyPlanItem.position",
    )


class DailyPlanItem(Base):
    __tablename__ = "daily_plan_items"
    __table_args__ = (UniqueConstraint("plan_id", "task_id", name="uq_plan_task"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("daily_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    automatically_added: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    plan: Mapped[DailyPlan] = relationship(back_populates="items")
    task: Mapped[Task] = relationship(lazy="selectin")


class Device(Base, TimestampMixin):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    fcm_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    certificate_subject: Mapped[str | None] = mapped_column(String(512), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatRequest(Base, TimestampMixin):
    __tablename__ = "chat_requests"
    __table_args__ = (Index("ix_chat_requests_queue", "status", "next_attempt_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    history: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list, nullable=False)
    tag_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=ChatRequestStatus.PENDING, nullable=False
    )
    answer: Mapped[str | None] = mapped_column(Text)
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceCursor(Base, TimestampMixin):
    __tablename__ = "source_cursors"
    __table_args__ = (UniqueConstraint("source_id", "cursor_key", name="uq_source_cursor"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("communication_sources.id", ondelete="CASCADE"), nullable=False
    )
    cursor_key: Mapped[str] = mapped_column(String(255), nullable=False)
    cursor_value: Mapped[str] = mapped_column(Text, nullable=False)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    firebase_credentials_encrypted: Mapped[str | None] = mapped_column(Text)


class ComponentStatus(Base, TimestampMixin):
    __tablename__ = "component_statuses"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    component_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CommunicationSourceTag(Base):
    __tablename__ = "communication_source_tags"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("communication_sources.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    sources: Mapped[list[CommunicationSource]] = relationship(
        secondary="communication_source_tags",
        back_populates="tags",
        lazy="selectin",
        passive_deletes=True,
    )


class CommunicationSource(Base, TimestampMixin):
    __tablename__ = "communication_sources"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    credential_encrypted: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    tags: Mapped[list[Tag]] = relationship(
        secondary="communication_source_tags",
        back_populates="sources",
        lazy="selectin",
        order_by="Tag.name",
        passive_deletes=True,
    )
