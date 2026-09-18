ALTER TABLE meeting_results ALTER COLUMN transcript_id DROP NOT NULL;

ALTER TABLE meeting_results ALTER COLUMN event_session_id DROP NOT NULL;

ALTER TABLE meeting_results ADD COLUMN parent_result_id UUID;

ALTER TABLE meeting_results ADD COLUMN origin_type VARCHAR(32) DEFAULT 'mts_transcript' NOT NULL;

ALTER TABLE meeting_results ADD COLUMN evidence TEXT;

ALTER TABLE meeting_results ADD CONSTRAINT fk_meeting_results_parent_result_id FOREIGN KEY(parent_result_id) REFERENCES meeting_results (id) ON DELETE SET NULL;

CREATE INDEX ix_meeting_results_parent_result_id ON meeting_results (parent_result_id);

UPDATE communication_events SET semantic_version = 2 WHERE event_type <> 'email' OR analysis_state <> 'COMPLETED';

