CREATE TABLE chat_requests (
    id UUID NOT NULL,
    query TEXT NOT NULL,
    history JSON DEFAULT '[]' NOT NULL,
    tag_ids JSON DEFAULT '[]' NOT NULL,
    status VARCHAR(32) DEFAULT 'PENDING' NOT NULL,
    answer TEXT,
    "references" JSON DEFAULT '[]' NOT NULL,
    error TEXT,
    attempts INTEGER DEFAULT '0' NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_chat_requests_next_attempt_at ON chat_requests (next_attempt_at);

CREATE INDEX ix_chat_requests_queue ON chat_requests (status, next_attempt_at, created_at);

