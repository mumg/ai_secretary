ALTER TABLE meetings ADD COLUMN mts_link_url TEXT;

ALTER TABLE meetings ADD COLUMN mts_link_keys JSON DEFAULT '[]' NOT NULL;

CREATE TABLE meeting_results (
    id UUID NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    transcript_id VARCHAR(128) NOT NULL,
    event_session_id VARCHAR(128) NOT NULL,
    activity_session_id VARCHAR(128),
    source_event_id UUID NOT NULL,
    calendar_meeting_id UUID,
    title VARCHAR(500) NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    owner_name VARCHAR(500),
    meeting_url TEXT,
    mts_link_keys JSON NOT NULL,
    transcript_status VARCHAR(64) NOT NULL,
    summary TEXT,
    decisions JSON NOT NULL,
    agreements JSON NOT NULL,
    analyzed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(calendar_meeting_id) REFERENCES meetings (id) ON DELETE SET NULL,
    FOREIGN KEY(source_event_id) REFERENCES communication_events (id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES communication_sources (id) ON DELETE CASCADE,
    UNIQUE (source_event_id),
    CONSTRAINT uq_meeting_result_source_transcript UNIQUE (source_id, transcript_id)
);

CREATE INDEX ix_meeting_results_calendar_meeting_id ON meeting_results (calendar_meeting_id);

CREATE INDEX ix_meeting_results_session ON meeting_results (event_session_id);

CREATE INDEX ix_meeting_results_time ON meeting_results (starts_at, id);

ALTER TABLE meeting_results ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ((to_tsvector('russian'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(owner_name, '') || ' ' || coalesce(decisions::text, '') || ' ' || coalesce(agreements::text, '')) || to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(owner_name, '') || ' ' || coalesce(decisions::text, '') || ' ' || coalesce(agreements::text, '')))) STORED;

CREATE INDEX ix_meeting_results_search_vector ON meeting_results USING gin (search_vector);

