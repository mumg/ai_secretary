CREATE TABLE delegations (
 id UUID PRIMARY KEY, title VARCHAR(500) NOT NULL, description TEXT,
 expected_result TEXT, assignee_name TEXT NOT NULL DEFAULT '', assignee_email TEXT NOT NULL DEFAULT '',
 status VARCHAR(32) NOT NULL DEFAULT 'ASSIGNED' CHECK(status IN ('ASSIGNED','IN_PROGRESS','IN_REVIEW','COMPLETED','CANCELLED')),
 due_at TIMESTAMPTZ, source_event_id UUID REFERENCES communication_events(id) ON DELETE SET NULL,
 evidence TEXT NOT NULL DEFAULT '', confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
 last_content_event_at TIMESTAMPTZ, last_status_event_at TIMESTAMPTZ, manual_status_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 search_vector TSVECTOR GENERATED ALWAYS AS (
 to_tsvector('russian',coalesce(title,'')||' '||coalesce(description,'')||' '||coalesce(expected_result,'')||' '||assignee_name||' '||assignee_email||' '||evidence) ||
 to_tsvector('simple',coalesce(title,'')||' '||coalesce(description,'')||' '||coalesce(expected_result,'')||' '||assignee_name||' '||assignee_email||' '||evidence)) STORED
);
CREATE INDEX ix_delegations_search ON delegations USING gin(search_vector);
CREATE INDEX ix_delegations_assignee ON delegations(assignee_email);
CREATE INDEX ix_delegations_source ON delegations(source_event_id);
CREATE TABLE delegation_history (
 id UUID PRIMARY KEY, delegation_id UUID NOT NULL REFERENCES delegations(id) ON DELETE CASCADE,
 source_event_id UUID REFERENCES communication_events(id) ON DELETE SET NULL,
 old_status TEXT, new_status TEXT NOT NULL, actor TEXT NOT NULL, explanation TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(delegation_id,source_event_id)
);
CREATE TRIGGER realtime_change AFTER INSERT OR UPDATE OR DELETE ON delegations
 FOR EACH ROW EXECUTE FUNCTION improver_notify_change('delegations');
