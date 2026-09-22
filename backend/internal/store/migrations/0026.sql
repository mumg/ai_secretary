-- The queue commits atomically with business changes; delivery is a worker job.
ALTER TABLE communication_events ADD COLUMN notification_history BOOLEAN NOT NULL DEFAULT false;
UPDATE communication_events e SET notification_history=true
FROM communication_sources s WHERE s.id=e.source_id AND e.occurred_at<=s.created_at;
CREATE TABLE notification_queue (
    object_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    object_id UUID NOT NULL,
    important BOOLEAN NOT NULL DEFAULT false,
    historical BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE notification_policy_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    next_send_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'
);
INSERT INTO notification_policy_state(id) VALUES(1);
CREATE TABLE notification_batches (
    id UUID PRIMARY KEY,
    kind TEXT NOT NULL,
    object_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE TABLE notification_deliveries (
    batch_id UUID NOT NULL REFERENCES notification_batches(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    notification_id UUID NOT NULL UNIQUE,
    accepted_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(batch_id,endpoint)
);
CREATE TABLE notification_batch_items (
    batch_id UUID NOT NULL REFERENCES notification_batches(id) ON DELETE CASCADE,
    object_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    object_id UUID NOT NULL,
    important BOOLEAN NOT NULL,
    historical BOOLEAN NOT NULL,
    PRIMARY KEY(batch_id,object_key)
);
