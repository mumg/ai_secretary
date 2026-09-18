ALTER TABLE communication_events ADD COLUMN is_mailing BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE communication_events ADD COLUMN mailing_confidence FLOAT;

ALTER TABLE communication_events ADD COLUMN mailing_kind VARCHAR(32);

ALTER TABLE communication_events ADD COLUMN mailing_version INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE communication_events ADD COLUMN mailing_analyzed_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_events_mailing_backfill ON communication_events (mailing_version, occurred_at) WHERE event_type = 'email' AND analysis_state = 'COMPLETED';

