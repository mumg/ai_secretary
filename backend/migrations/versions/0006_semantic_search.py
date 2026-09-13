"""Add the prepared semantic search index.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("communication_events", sa.Column("semantic_summary", sa.Text()))
    for name in (
        "semantic_categories",
        "semantic_keywords",
        "semantic_people",
        "semantic_organizations",
        "semantic_decisions",
        "semantic_agreements",
    ):
        op.add_column(
            "communication_events",
            sa.Column(
                name,
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'::json"),
            ),
        )
    op.add_column("communication_events", sa.Column("semantic_index", sa.Text()))
    op.add_column(
        "communication_events",
        sa.Column("semantic_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_events_semantic_trgm ON communication_events "
        "USING gin (semantic_index gin_trgm_ops)"
    )
    op.create_index(
        "ix_events_thread_occurred",
        "communication_events",
        ["source_id", "thread_external_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_events_thread_occurred", table_name="communication_events")
    op.execute("DROP INDEX IF EXISTS ix_events_semantic_trgm")
    op.drop_column("communication_events", "semantic_version")
    op.drop_column("communication_events", "semantic_index")
    for name in reversed(
        (
            "semantic_categories",
            "semantic_keywords",
            "semantic_people",
            "semantic_organizations",
            "semantic_decisions",
            "semantic_agreements",
        )
    ):
        op.drop_column("communication_events", name)
    op.drop_column("communication_events", "semantic_summary")
