"""Persist normalized email subjects and their token sets."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("communication_events", sa.Column("subject_key", sa.String(80)))
    op.add_column(
        "communication_events",
        sa.Column(
            "subject_tokens",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_index("ix_events_subject", "communication_events", ["source_id", "subject_key"])


def downgrade() -> None:
    op.drop_index("ix_events_subject", table_name="communication_events")
    op.drop_column("communication_events", "subject_tokens")
    op.drop_column("communication_events", "subject_key")
