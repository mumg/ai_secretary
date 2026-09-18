ALTER TABLE communication_events ADD COLUMN analysis_model VARCHAR(255);

ALTER TABLE communication_events ADD COLUMN analysis_result JSON;

