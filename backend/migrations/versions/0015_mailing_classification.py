"""Add durable Qwen mailing classification.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "communication_events",
        sa.Column("is_mailing", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "communication_events",
        sa.Column("mailing_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "communication_events",
        sa.Column("mailing_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "communication_events",
        sa.Column("mailing_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "communication_events",
        sa.Column("mailing_analyzed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_events_mailing_backfill",
        "communication_events",
        ["mailing_version", "occurred_at"],
        postgresql_where=sa.text(
            "event_type = 'email' AND analysis_state = 'COMPLETED'"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_events_mailing_backfill", table_name="communication_events")
    op.drop_column("communication_events", "mailing_analyzed_at")
    op.drop_column("communication_events", "mailing_version")
    op.drop_column("communication_events", "mailing_kind")
    op.drop_column("communication_events", "mailing_confidence")
    op.drop_column("communication_events", "is_mailing")
