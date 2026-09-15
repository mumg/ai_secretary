"""Commit-bound invalidations for all API/worker/importer changes."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TABLES = {
    "tasks": "tasks",
    "reminders": "tasks",
    "daily_plans": "tasks",
    "meetings": "meetings",
    "meeting_contexts": "contexts",
    "meeting_results": "results",
    "conversation_threads": "threads",
    "communication_events": "events",
    "attachments": "events",
    "chat_requests": "chat",
    "component_statuses": "status",
    "communication_sources": "all",
    "system_settings": "all",
}


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION improver_notify_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND
                (to_jsonb(OLD) - ARRAY['updated_at', 'ranking_score', 'ranking_reasons',
                    'notify_after', 'next_refresh_at']) IS NOT DISTINCT FROM
                (to_jsonb(NEW) - ARRAY['updated_at', 'ranking_score', 'ranking_reasons',
                    'notify_after', 'next_refresh_at']) THEN
                RETURN NULL;
            END IF;
            -- Identical topic payloads coalesce within the transaction. No archive
            -- text or credentials enter NOTIFY. Delivery occurs only after COMMIT.
            PERFORM pg_notify('improver_changes', TG_ARGV[0]);
            RETURN NULL;
        END;
        $$
    """)
    for table, topic in TABLES.items():
        op.execute(f"""
            CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('{topic}')
        """)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER realtime_change ON {table}")
    op.execute("DROP FUNCTION improver_notify_change()")
