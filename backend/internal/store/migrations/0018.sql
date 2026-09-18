CREATE TABLE meeting_contexts (
    meeting_id UUID NOT NULL,
    status VARCHAR(32) DEFAULT 'PENDING' NOT NULL,
    summary TEXT,
    "references" JSON DEFAULT '[]' NOT NULL,
    meeting_fingerprint VARCHAR(64) NOT NULL,
    input_fingerprint VARCHAR(64),
    generation UUID,
    started_at TIMESTAMP WITH TIME ZONE,
    generated_at TIMESTAMP WITH TIME ZONE,
    next_refresh_at TIMESTAMP WITH TIME ZONE,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (meeting_id),
    FOREIGN KEY(meeting_id) REFERENCES meetings (id) ON DELETE CASCADE
);

CREATE INDEX ix_meeting_contexts_next_refresh_at ON meeting_contexts (next_refresh_at);

