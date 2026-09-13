"""Add continuously summarized conversation threads.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("thread_external_id", sa.String(512), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column(
            "participants",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("summary", sa.Text()),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_event_id", sa.Uuid()),
        sa.Column("summary_model", sa.String(255)),
        sa.Column("summarized_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["latest_event_id"],
            ["communication_events.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "thread_external_id",
            name="uq_conversation_thread_source_external",
        ),
    )
    op.create_index(
        "ix_conversation_threads_recent",
        "conversation_threads",
        ["last_event_at", "id"],
    )
    op.create_index(
        "ix_conversation_threads_latest_event_id",
        "conversation_threads",
        ["latest_event_id"],
    )

    op.execute(
        "UPDATE communication_events "
        "SET thread_external_id = external_id "
        "WHERE thread_external_id IS NULL OR thread_external_id = ''"
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                event.*,
                row_number() OVER (
                    PARTITION BY event.source_id, event.thread_external_id
                    ORDER BY event.occurred_at DESC, event.id DESC
                ) AS position,
                min(event.occurred_at) OVER (
                    PARTITION BY event.source_id, event.thread_external_id
                ) AS first_event_at,
                count(*) OVER (
                    PARTITION BY event.source_id, event.thread_external_id
                ) AS event_count
            FROM communication_events AS event
        )
        INSERT INTO conversation_threads (
            id,
            source_id,
            source_type,
            thread_external_id,
            title,
            participants,
            summary,
            event_count,
            first_event_at,
            last_event_at,
            latest_event_id,
            summary_model,
            summarized_at
        )
        SELECT
            gen_random_uuid(),
            source_id,
            source_type,
            thread_external_id,
            subject,
            participants,
            NULLIF(btrim(semantic_summary), ''),
            event_count,
            first_event_at,
            occurred_at,
            id,
            analysis_model,
            analyzed_at
        FROM ranked
        WHERE position = 1
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_threads_latest_event_id",
        table_name="conversation_threads",
    )
    op.drop_index("ix_conversation_threads_recent", table_name="conversation_threads")
    op.drop_table("conversation_threads")
