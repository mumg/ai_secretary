CREATE TABLE diagnostic_recommendations (
    correction_id UUID PRIMARY KEY,
    report_id UUID NOT NULL REFERENCES diagnostic_reports(report_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    event_id BIGINT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('offered','applied','declined','superseded')) DEFAULT 'offered',
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    UNIQUE(report_id,event_id)
);
CREATE INDEX diagnostic_recommendations_report ON diagnostic_recommendations(report_id);
ALTER TABLE llm_temporary_corrections ADD COLUMN recommendation_revision INTEGER NOT NULL DEFAULT 1;
