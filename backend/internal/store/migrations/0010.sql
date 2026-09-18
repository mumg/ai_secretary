CREATE TABLE meetings (
    id UUID NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    external_uid VARCHAR(512) NOT NULL,
    source_event_id UUID NOT NULL,
    title VARCHAR(500) NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    all_day BOOLEAN NOT NULL,
    location TEXT,
    organizer JSON,
    attendees JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    method VARCHAR(32) NOT NULL,
    last_event_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(source_event_id) REFERENCES communication_events (id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES communication_sources (id) ON DELETE CASCADE,
    CONSTRAINT uq_meeting_source_uid UNIQUE (source_id, external_uid)
);

CREATE INDEX ix_meetings_source_event_id ON meetings (source_event_id);

CREATE INDEX ix_meetings_time ON meetings (starts_at, ends_at);

