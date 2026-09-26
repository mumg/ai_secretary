-- Only recognized systemic issues accepted into the release backlog appear here.
-- Removing eligibility does not erase the audit trail or individual corrections.
CREATE TABLE llm_system_issues (
    issue_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    backlog_accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    release_fix_eligible BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE llm_temporary_corrections (
    correction_id UUID PRIMARY KEY,
    report_id UUID,
    scope TEXT NOT NULL CHECK (scope IN ('individual', 'systemic')),
    issue_id TEXT REFERENCES llm_system_issues(issue_id),
    CONSTRAINT llm_correction_issue_scope CHECK (
        (scope = 'individual' AND issue_id IS NULL) OR
        (scope = 'systemic' AND issue_id IS NOT NULL)
    ),
    request_type TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    template_revision TEXT NOT NULL,
    personal_correction_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active', 'manual_disabled', 'disabled_by_fix', 'needs_review')),
    status_reason TEXT NOT NULL DEFAULT '',
    fix_id TEXT NOT NULL DEFAULT '',
    fixed_version TEXT NOT NULL DEFAULT '',
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX llm_temporary_corrections_status ON llm_temporary_corrections(status, request_type);

CREATE TABLE llm_temporary_correction_history (
    id BIGSERIAL PRIMARY KEY,
    correction_id UUID NOT NULL REFERENCES llm_temporary_corrections(correction_id),
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    fix_id TEXT NOT NULL DEFAULT '',
    release_version TEXT NOT NULL DEFAULT '',
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
