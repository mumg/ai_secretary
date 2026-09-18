CREATE TABLE communication_events (
    id UUID NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    external_id VARCHAR(512) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    thread_external_id VARCHAR(512),
    subject TEXT,
    author VARCHAR(512),
    participants JSON NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    body TEXT NOT NULL,
    source_url TEXT,
    raw_headers JSON NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    analysis_state VARCHAR(32) NOT NULL,
    analysis_error TEXT,
    analyzed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_event_source_external UNIQUE (source_id, external_id)
);

CREATE INDEX ix_events_analysis ON communication_events (analysis_state, occurred_at);

CREATE INDEX ix_events_thread ON communication_events (source_id, thread_external_id);

CREATE INDEX ix_communication_events_content_hash ON communication_events (content_hash);

CREATE TABLE attachments (
    id UUID NOT NULL,
    event_id UUID NOT NULL,
    filename VARCHAR(512) NOT NULL,
    media_type VARCHAR(255) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_path TEXT NOT NULL,
    extraction_state VARCHAR(32) NOT NULL,
    extracted_text TEXT,
    extraction_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(event_id) REFERENCES communication_events (id) ON DELETE CASCADE
);

CREATE INDEX ix_attachments_event_id ON attachments (event_id);

CREATE INDEX ix_attachments_sha256 ON attachments (sha256);

CREATE TABLE tasks (
    id UUID NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(32) NOT NULL,
    priority VARCHAR(16) NOT NULL,
    priority_source VARCHAR(16) NOT NULL,
    due_at TIMESTAMP WITH TIME ZONE,
    source_event_id UUID,
    evidence TEXT,
    confidence FLOAT,
    ranking_score FLOAT NOT NULL,
    ranking_reasons JSON NOT NULL,
    manually_created BOOLEAN NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(source_event_id) REFERENCES communication_events (id) ON DELETE SET NULL
);

CREATE INDEX ix_tasks_active_rank ON tasks (status, ranking_score);

CREATE INDEX ix_tasks_due ON tasks (due_at);

CREATE INDEX ix_tasks_source_event_id ON tasks (source_event_id);

CREATE TABLE reminders (
    id UUID NOT NULL,
    task_id UUID NOT NULL,
    remind_at TIMESTAMP WITH TIME ZONE NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

CREATE INDEX ix_reminders_due ON reminders (sent_at, remind_at);

CREATE INDEX ix_reminders_task_id ON reminders (task_id);

CREATE TABLE daily_plans (
    id UUID NOT NULL,
    plan_date DATE NOT NULL,
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (plan_date)
);

CREATE TABLE daily_plan_items (
    id UUID NOT NULL,
    plan_id UUID NOT NULL,
    task_id UUID NOT NULL,
    position INTEGER NOT NULL,
    pinned BOOLEAN NOT NULL,
    automatically_added BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(plan_id) REFERENCES daily_plans (id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE,
    CONSTRAINT uq_plan_task UNIQUE (plan_id, task_id)
);

CREATE INDEX ix_daily_plan_items_plan_id ON daily_plan_items (plan_id);

CREATE INDEX ix_daily_plan_items_task_id ON daily_plan_items (task_id);

CREATE TABLE devices (
    id UUID NOT NULL,
    label VARCHAR(255) NOT NULL,
    fcm_token TEXT NOT NULL,
    certificate_subject VARCHAR(512),
    active BOOLEAN NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (certificate_subject),
    UNIQUE (fcm_token)
);

CREATE TABLE source_cursors (
    id UUID NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    cursor_key VARCHAR(255) NOT NULL,
    cursor_value TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_source_cursor UNIQUE (source_id, cursor_key)
);

