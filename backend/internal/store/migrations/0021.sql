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
        $$;

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON tasks
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('tasks');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON reminders
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('tasks');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON daily_plans
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('tasks');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON meetings
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('meetings');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON meeting_contexts
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('contexts');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON meeting_results
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('results');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON conversation_threads
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('threads');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON communication_events
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('events');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON attachments
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('events');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON chat_requests
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('chat');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON component_statuses
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('status');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON communication_sources
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('all');

CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON system_settings
            FOR EACH ROW EXECUTE FUNCTION improver_notify_change('all');

