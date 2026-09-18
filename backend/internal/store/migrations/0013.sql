ALTER TABLE communication_events ADD COLUMN analysis_attempts INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE communication_events ADD COLUMN next_analysis_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_communication_events_next_analysis_at ON communication_events (next_analysis_at);

UPDATE communication_events SET analysis_state = 'PENDING', next_analysis_at = now() WHERE analysis_state = 'FAILED';

UPDATE communication_events SET semantic_version = 0, next_analysis_at = now() WHERE semantic_version < 0;

