"""Add MTS Link transcript meeting results.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _search_vector(document: str) -> str:
    return (
        "(to_tsvector('russian'::regconfig, "
        f"{document}) || to_tsvector('simple'::regconfig, {document}))"
    )


def upgrade() -> None:
    op.add_column("meetings", sa.Column("mts_link_url", sa.Text(), nullable=True))
    op.add_column(
        "meetings",
        sa.Column("mts_link_keys", sa.JSON(), server_default="[]", nullable=False),
    )
    op.create_table(
        "meeting_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("transcript_id", sa.String(length=128), nullable=False),
        sa.Column("event_session_id", sa.String(length=128), nullable=False),
        sa.Column("activity_session_id", sa.String(length=128), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("calendar_meeting_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_name", sa.String(length=500), nullable=True),
        sa.Column("meeting_url", sa.Text(), nullable=True),
        sa.Column("mts_link_keys", sa.JSON(), nullable=False),
        sa.Column("transcript_status", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("agreements", sa.JSON(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_meeting_id"], ["meetings.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_event_id"], ["communication_events.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["communication_sources.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id"),
        sa.UniqueConstraint(
            "source_id", "transcript_id", name="uq_meeting_result_source_transcript"
        ),
    )
    op.create_index(
        "ix_meeting_results_calendar_meeting_id",
        "meeting_results",
        ["calendar_meeting_id"],
    )
    op.create_index(
        "ix_meeting_results_session", "meeting_results", ["event_session_id"]
    )
    op.create_index(
        "ix_meeting_results_time", "meeting_results", ["starts_at", "id"]
    )
    document = (
        "coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || "
        "coalesce(owner_name, '') || ' ' || coalesce(decisions::text, '') || ' ' || "
        "coalesce(agreements::text, '')"
    )
    op.execute(
        "ALTER TABLE meeting_results ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({_search_vector(document)}) STORED"
    )
    op.execute(
        "CREATE INDEX ix_meeting_results_search_vector ON meeting_results "
        "USING gin (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_meeting_results_search_vector")
    op.drop_index("ix_meeting_results_time", table_name="meeting_results")
    op.drop_index("ix_meeting_results_session", table_name="meeting_results")
    op.drop_index("ix_meeting_results_calendar_meeting_id", table_name="meeting_results")
    op.drop_table("meeting_results")
    op.drop_column("meetings", "mts_link_keys")
    op.drop_column("meetings", "mts_link_url")
