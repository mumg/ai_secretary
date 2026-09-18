ALTER TABLE communication_events ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, coalesce(subject, '') || ' ' || coalesce(author, '') || ' ' || coalesce(participants::text, '') || ' ' || coalesce(semantic_index, '') || ' ' || coalesce(body, '')) || to_tsvector('simple'::regconfig, coalesce(subject, '') || ' ' || coalesce(author, '') || ' ' || coalesce(participants::text, '') || ' ' || coalesce(semantic_index, '') || ' ' || coalesce(body, ''))) STORED;

CREATE INDEX ix_communication_events_search_vector ON communication_events USING gin (search_vector);

ALTER TABLE attachments ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, coalesce(filename, '') || ' ' || coalesce(extracted_text, '')) || to_tsvector('simple'::regconfig, coalesce(filename, '') || ' ' || coalesce(extracted_text, ''))) STORED;

CREATE INDEX ix_attachments_search_vector ON attachments USING gin (search_vector);

