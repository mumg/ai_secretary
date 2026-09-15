"""Add durable asynchronous chat requests.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("history", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("tag_ids", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="PENDING", nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("references", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_requests_next_attempt_at",
        "chat_requests",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_chat_requests_queue",
        "chat_requests",
        ["status", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_requests_queue", table_name="chat_requests")
    op.drop_index("ix_chat_requests_next_attempt_at", table_name="chat_requests")
    op.drop_table("chat_requests")
