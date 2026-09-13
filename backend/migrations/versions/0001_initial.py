"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "communication_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("thread_external_id", sa.String(512)),
        sa.Column("subject", sa.Text()),
        sa.Column("author", sa.String(512)),
        sa.Column("participants", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_headers", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("analysis_state", sa.String(32), nullable=False),
        sa.Column("analysis_error", sa.Text()),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_event_source_external"),
    )
    op.create_index("ix_events_analysis", "communication_events", ["analysis_state", "occurred_at"])
    op.create_index("ix_events_thread", "communication_events", ["source_id", "thread_external_id"])
    op.create_index("ix_communication_events_content_hash", "communication_events", ["content_hash"])

    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("extraction_state", sa.String(32), nullable=False),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("extraction_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["communication_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachments_event_id", "attachments", ["event_id"])
    op.create_index("ix_attachments_sha256", "attachments", ["sha256"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("priority_source", sa.String(16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("source_event_id", sa.Uuid()),
        sa.Column("evidence", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("ranking_score", sa.Float(), nullable=False),
        sa.Column("ranking_reasons", sa.JSON(), nullable=False),
        sa.Column("manually_created", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_event_id"], ["communication_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_active_rank", "tasks", ["status", "ranking_score"])
    op.create_index("ix_tasks_due", "tasks", ["due_at"])
    op.create_index("ix_tasks_source_event_id", "tasks", ["source_event_id"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reminders_due", "reminders", ["sent_at", "remind_at"])
    op.create_index("ix_reminders_task_id", "reminders", ["task_id"])

    op.create_table(
        "daily_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_date"),
    )

    op.create_table(
        "daily_plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("automatically_added", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["daily_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "task_id", name="uq_plan_task"),
    )
    op.create_index("ix_daily_plan_items_plan_id", "daily_plan_items", ["plan_id"])
    op.create_index("ix_daily_plan_items_task_id", "daily_plan_items", ["task_id"])

    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("fcm_token", sa.Text(), nullable=False),
        sa.Column("certificate_subject", sa.String(512)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("certificate_subject"),
        sa.UniqueConstraint("fcm_token"),
    )

    op.create_table(
        "source_cursors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("cursor_key", sa.String(255), nullable=False),
        sa.Column("cursor_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "cursor_key", name="uq_source_cursor"),
    )


def downgrade() -> None:
    op.drop_table("source_cursors")
    op.drop_table("devices")
    op.drop_table("daily_plan_items")
    op.drop_table("daily_plans")
    op.drop_table("reminders")
    op.drop_table("tasks")
    op.drop_table("attachments")
    op.drop_table("communication_events")

