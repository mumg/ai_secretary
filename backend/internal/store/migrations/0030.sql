CREATE TABLE diagnostic_reports (
    report_id UUID PRIMARY KEY,
    payload_cipher TEXT NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','received','in_review','resolved','rejected')),
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX diagnostic_reports_created ON diagnostic_reports(created_at DESC);
