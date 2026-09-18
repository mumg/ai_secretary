CREATE TABLE conversation_threads (
    id UUID NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    thread_external_id VARCHAR(512) NOT NULL,
    title TEXT,
    participants JSON DEFAULT '[]'::json NOT NULL,
    summary TEXT,
    event_count INTEGER DEFAULT '0' NOT NULL,
    first_event_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_event_at TIMESTAMP WITH TIME ZONE NOT NULL,
    latest_event_id UUID,
    summary_model VARCHAR(255),
    summarized_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(latest_event_id) REFERENCES communication_events (id) ON DELETE SET NULL,
    CONSTRAINT uq_conversation_thread_source_external UNIQUE (source_id, thread_external_id)
);

CREATE INDEX ix_conversation_threads_recent ON conversation_threads (last_event_at, id);

CREATE INDEX ix_conversation_threads_latest_event_id ON conversation_threads (latest_event_id);

UPDATE communication_events SET thread_external_id = external_id WHERE thread_external_id IS NULL OR thread_external_id = '';

WITH ranked AS (
            SELECT
                event.*,
                row_number() OVER (
                    PARTITION BY event.source_id, event.thread_external_id
                    ORDER BY event.occurred_at DESC, event.id DESC
                ) AS position,
                min(event.occurred_at) OVER (
                    PARTITION BY event.source_id, event.thread_external_id
                ) AS first_event_at,
                count(*) OVER (
                    PARTITION BY event.source_id, event.thread_external_id
                ) AS event_count
            FROM communication_events AS event
        )
        INSERT INTO conversation_threads (
            id,
            source_id,
            source_type,
            thread_external_id,
            title,
            participants,
            summary,
            event_count,
            first_event_at,
            last_event_at,
            latest_event_id,
            summary_model,
            summarized_at
        )
        SELECT
            gen_random_uuid(),
            source_id,
            source_type,
            thread_external_id,
            subject,
            participants,
            NULLIF(btrim(semantic_summary), ''),
            event_count,
            first_event_at,
            occurred_at,
            id,
            analysis_model,
            analyzed_at
        FROM ranked
        WHERE position = 1;

