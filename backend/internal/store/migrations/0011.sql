ALTER TABLE tasks ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ((to_tsvector('russian'::regconfig, coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(evidence, '')) || to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(evidence, '')))) STORED;

CREATE INDEX ix_tasks_search_vector ON tasks USING gin (search_vector);

ALTER TABLE meetings ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ((to_tsvector('russian'::regconfig, coalesce(title, '') || ' ' || coalesce(location, '') || ' ' || coalesce(organizer::text, '') || ' ' || coalesce(attendees::text, '')) || to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(location, '') || ' ' || coalesce(organizer::text, '') || ' ' || coalesce(attendees::text, '')))) STORED;

CREATE INDEX ix_meetings_search_vector ON meetings USING gin (search_vector);

ALTER TABLE conversation_threads ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ((to_tsvector('russian'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(participants::text, '')) || to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(participants::text, '')))) STORED;

CREATE INDEX ix_conversation_threads_search_vector ON conversation_threads USING gin (search_vector);

