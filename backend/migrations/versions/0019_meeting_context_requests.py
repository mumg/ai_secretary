"""Limit automatic preparation to today's plan and persist manual requests/push delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meeting_contexts", sa.Column("requested_at", sa.DateTime(timezone=True)))
    op.add_column("meeting_contexts", sa.Column("notify_after", sa.DateTime(timezone=True)))
    op.create_index("ix_meeting_contexts_notify_after", "meeting_contexts", ["notify_after"])
    op.alter_column("meeting_contexts", "status", server_default="NOT_REQUESTED")
    # Stop the old unbounded background queue. Today's plan will re-enqueue its meetings.
    op.execute(
        "UPDATE meeting_contexts SET status = 'NOT_REQUESTED', generation = NULL, "
        "started_at = NULL WHERE status IN ('PENDING', 'PROCESSING')"
    )


def downgrade() -> None:
    op.alter_column("meeting_contexts", "status", server_default="PENDING")
    op.execute("UPDATE meeting_contexts SET status = 'PENDING' WHERE status = 'NOT_REQUESTED'")
    op.drop_index("ix_meeting_contexts_notify_after", table_name="meeting_contexts")
    op.drop_column("meeting_contexts", "notify_after")
    op.drop_column("meeting_contexts", "requested_at")
