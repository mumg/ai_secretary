"""Persist preparation context for scheduled meetings."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_contexts",
        sa.Column(
            "meeting_id",
            sa.Uuid(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("summary", sa.Text()),
        sa.Column("references", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("meeting_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64)),
        sa.Column("generation", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_meeting_contexts_next_refresh_at", "meeting_contexts", ["next_refresh_at"])


def downgrade() -> None:
    op.drop_table("meeting_contexts")
