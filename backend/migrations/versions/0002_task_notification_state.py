"""Add automatic task notification state.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("due_reminder_sent_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("overdue_notification_date", sa.Date()))


def downgrade() -> None:
    op.drop_column("tasks", "overdue_notification_date")
    op.drop_column("tasks", "due_reminder_sent_at")
