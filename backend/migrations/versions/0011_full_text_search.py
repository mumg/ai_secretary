"""Add full-text search indexes for tasks, meetings and conversation threads.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _search_vector(document: str) -> str:
    return (
        "(to_tsvector('russian'::regconfig, "
        f"{document}) || to_tsvector('simple'::regconfig, {document}))"
    )


def upgrade() -> None:
    task_document = (
        "coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || "
        "coalesce(evidence, '')"
    )
    meeting_document = (
        "coalesce(title, '') || ' ' || coalesce(location, '') || ' ' || "
        "coalesce(organizer::text, '') || ' ' || coalesce(attendees::text, '')"
    )
    thread_document = (
        "coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || "
        "coalesce(participants::text, '')"
    )
    for table, document in (
        ("tasks", task_document),
        ("meetings", meeting_document),
        ("conversation_threads", thread_document),
    ):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN search_vector tsvector "
            f"GENERATED ALWAYS AS ({_search_vector(document)}) STORED"
        )
        op.execute(
            f"CREATE INDEX ix_{table}_search_vector ON {table} "
            "USING gin (search_vector)"
        )


def downgrade() -> None:
    for table in ("conversation_threads", "meetings", "tasks"):
        op.execute(f"DROP INDEX ix_{table}_search_vector")
        op.execute(f"ALTER TABLE {table} DROP COLUMN search_vector")
