DELETE FROM tasks
        WHERE source_event_id IN (
            SELECT event.id
            FROM communication_events AS event
            LEFT JOIN communication_sources AS source ON source.id = event.source_id
            WHERE source.id IS NULL
        );

DELETE FROM conversation_threads WHERE source_id NOT IN (SELECT id FROM communication_sources);

DELETE FROM communication_events WHERE source_id NOT IN (SELECT id FROM communication_sources);

DELETE FROM source_cursors WHERE source_id NOT IN (SELECT id FROM communication_sources);

ALTER TABLE tasks DROP CONSTRAINT tasks_source_event_id_fkey;

ALTER TABLE tasks ADD CONSTRAINT fk_tasks_source_event_cascade FOREIGN KEY(source_event_id) REFERENCES communication_events (id) ON DELETE CASCADE;

ALTER TABLE communication_events ADD CONSTRAINT fk_events_source_cascade FOREIGN KEY(source_id) REFERENCES communication_sources (id) ON DELETE CASCADE;

ALTER TABLE conversation_threads ADD CONSTRAINT fk_threads_source_cascade FOREIGN KEY(source_id) REFERENCES communication_sources (id) ON DELETE CASCADE;

ALTER TABLE source_cursors ADD CONSTRAINT fk_source_cursors_source_cascade FOREIGN KEY(source_id) REFERENCES communication_sources (id) ON DELETE CASCADE;

