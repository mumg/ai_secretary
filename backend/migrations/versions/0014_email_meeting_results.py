"""Merge email follow-ups into meeting results.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("meeting_results", "transcript_id", nullable=True)
    op.alter_column("meeting_results", "event_session_id", nullable=True)
    op.add_column(
        "meeting_results",
        sa.Column("parent_result_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "meeting_results",
        sa.Column(
            "origin_type",
            sa.String(length=32),
            server_default="mts_transcript",
            nullable=False,
        ),
    )
    op.add_column("meeting_results", sa.Column("evidence", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_meeting_results_parent_result_id",
        "meeting_results",
        "meeting_results",
        ["parent_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_meeting_results_parent_result_id",
        "meeting_results",
        ["parent_result_id"],
    )
    # Reclassify existing ordinary emails, while avoiding a costly pass over artifacts
    # that cannot become participant meeting summaries.
    op.execute(
        "UPDATE communication_events SET semantic_version = 2 "
        "WHERE event_type <> 'email' OR analysis_state <> 'COMPLETED'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM meeting_results "
        "WHERE transcript_id IS NULL OR event_session_id IS NULL"
    )
    op.drop_index("ix_meeting_results_parent_result_id", table_name="meeting_results")
    op.drop_constraint(
        "fk_meeting_results_parent_result_id",
        "meeting_results",
        type_="foreignkey",
    )
    op.drop_column("meeting_results", "evidence")
    op.drop_column("meeting_results", "origin_type")
    op.drop_column("meeting_results", "parent_result_id")
    op.alter_column("meeting_results", "event_session_id", nullable=False)
    op.alter_column("meeting_results", "transcript_id", nullable=False)
