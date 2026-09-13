"""Add the application database schema version gate.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    version_table = op.create_table(
        "database_schema_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_database_schema_version_singleton"),
        sa.CheckConstraint("version >= 0", name="ck_database_schema_version_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        version_table,
        [{"id": 1, "version": 7, "revision": revision}],
    )


def downgrade() -> None:
    op.drop_table("database_schema_version")
