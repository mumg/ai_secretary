ALTER TABLE communication_events ADD COLUMN subject_key VARCHAR(80);

ALTER TABLE communication_events ADD COLUMN subject_tokens JSON DEFAULT '[]' NOT NULL;

CREATE INDEX ix_events_subject ON communication_events (source_id, subject_key);

