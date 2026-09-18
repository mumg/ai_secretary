CREATE TABLE database_schema_version (
    id SERIAL NOT NULL,
    version INTEGER NOT NULL,
    revision VARCHAR(64) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_database_schema_version_singleton CHECK (id = 1),
    CONSTRAINT ck_database_schema_version_nonnegative CHECK (version >= 0)
);

INSERT INTO database_schema_version (id, version, revision) VALUES (1, 7, '0007');

