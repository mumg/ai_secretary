"""Persist the LLM model and structured analysis result.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("communication_events", sa.Column("analysis_model", sa.String(255)))
    op.add_column("communication_events", sa.Column("analysis_result", sa.JSON()))


def downgrade() -> None:
    op.drop_column("communication_events", "analysis_result")
    op.drop_column("communication_events", "analysis_model")
