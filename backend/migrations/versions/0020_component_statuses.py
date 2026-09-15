"""Persist component heartbeats, errors, and numeric metrics."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "component_statuses",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("component_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_component_statuses_expires_at", "component_statuses", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_component_statuses_expires_at", table_name="component_statuses")
    op.drop_table("component_statuses")
