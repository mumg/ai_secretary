ALTER TABLE communication_events ADD COLUMN semantic_summary TEXT;

ALTER TABLE communication_events ADD COLUMN semantic_categories JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE communication_events ADD COLUMN semantic_keywords JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE communication_events ADD COLUMN semantic_people JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE communication_events ADD COLUMN semantic_organizations JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE communication_events ADD COLUMN semantic_decisions JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE communication_events ADD COLUMN semantic_agreements JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE communication_events ADD COLUMN semantic_index TEXT;

ALTER TABLE communication_events ADD COLUMN semantic_version INTEGER DEFAULT '0' NOT NULL;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX ix_events_semantic_trgm ON communication_events USING gin (semantic_index gin_trgm_ops);

CREATE INDEX ix_events_thread_occurred ON communication_events (source_id, thread_external_id, occurred_at);

