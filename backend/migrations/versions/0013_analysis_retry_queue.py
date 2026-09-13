"""Add a durable retry queue for transient LLM failures.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "communication_events",
        sa.Column(
            "analysis_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "communication_events",
        sa.Column("next_analysis_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_communication_events_next_analysis_at",
        "communication_events",
        ["next_analysis_at"],
    )
    op.execute(
        "UPDATE communication_events SET analysis_state = 'PENDING', "
        "next_analysis_at = now() WHERE analysis_state = 'FAILED'"
    )
    op.execute(
        "UPDATE communication_events SET semantic_version = 0, "
        "next_analysis_at = now() WHERE semantic_version < 0"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_communication_events_next_analysis_at",
        table_name="communication_events",
    )
    op.drop_column("communication_events", "next_analysis_at")
    op.drop_column("communication_events", "analysis_attempts")
