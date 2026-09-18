ALTER TABLE meeting_contexts ADD COLUMN requested_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE meeting_contexts ADD COLUMN notify_after TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_meeting_contexts_notify_after ON meeting_contexts (notify_after);

ALTER TABLE meeting_contexts ALTER COLUMN status SET DEFAULT 'NOT_REQUESTED';

UPDATE meeting_contexts SET status = 'NOT_REQUESTED', generation = NULL, started_at = NULL WHERE status IN ('PENDING', 'PROCESSING');

