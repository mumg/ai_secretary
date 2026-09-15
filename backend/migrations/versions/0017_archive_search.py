"""Index archive originals and attachments for chat retrieval.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    documents = {
        "communication_events": (
            "coalesce(subject, '') || ' ' || coalesce(author, '') || ' ' || "
            "coalesce(participants::text, '') || ' ' || coalesce(semantic_index, '') || ' ' || "
            "coalesce(body, '')"
        ),
        "attachments": "coalesce(filename, '') || ' ' || coalesce(extracted_text, '')",
    }
    for table, document in documents.items():
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN search_vector tsvector GENERATED ALWAYS AS "
            f"(to_tsvector('russian'::regconfig, {document}) || "
            f"to_tsvector('simple'::regconfig, {document})) STORED"
        )
        op.execute(f"CREATE INDEX ix_{table}_search_vector ON {table} USING gin (search_vector)")


def downgrade() -> None:
    for table in ("attachments", "communication_events"):
        op.execute(f"DROP INDEX ix_{table}_search_vector")
        op.execute(f"ALTER TABLE {table} DROP COLUMN search_vector")
