"""Cascade source deletion through imported data.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tasks must be removed before their orphaned events while the old FK still uses SET NULL.
    op.execute(
        """
        DELETE FROM tasks
        WHERE source_event_id IN (
            SELECT event.id
            FROM communication_events AS event
            LEFT JOIN communication_sources AS source ON source.id = event.source_id
            WHERE source.id IS NULL
        )
        """
    )
    op.execute(
        "DELETE FROM conversation_threads WHERE source_id NOT IN "
        "(SELECT id FROM communication_sources)"
    )
    op.execute(
        "DELETE FROM communication_events WHERE source_id NOT IN "
        "(SELECT id FROM communication_sources)"
    )
    op.execute(
        "DELETE FROM source_cursors WHERE source_id NOT IN "
        "(SELECT id FROM communication_sources)"
    )

    op.drop_constraint("tasks_source_event_id_fkey", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_tasks_source_event_cascade",
        "tasks",
        "communication_events",
        ["source_event_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_events_source_cascade",
        "communication_events",
        "communication_sources",
        ["source_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_threads_source_cascade",
        "conversation_threads",
        "communication_sources",
        ["source_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_source_cursors_source_cascade",
        "source_cursors",
        "communication_sources",
        ["source_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_source_cursors_source_cascade", "source_cursors", type_="foreignkey")
    op.drop_constraint("fk_threads_source_cascade", "conversation_threads", type_="foreignkey")
    op.drop_constraint("fk_events_source_cascade", "communication_events", type_="foreignkey")
    op.drop_constraint("fk_tasks_source_event_cascade", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "tasks_source_event_id_fkey",
        "tasks",
        "communication_events",
        ["source_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
