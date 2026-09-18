CREATE TABLE tags (
    id UUID NOT NULL,
    name VARCHAR(100) NOT NULL,
    normalized_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_tags_normalized_name UNIQUE (normalized_name)
);

CREATE TABLE communication_source_tags (
    source_id VARCHAR(128) NOT NULL,
    tag_id UUID NOT NULL,
    PRIMARY KEY (source_id, tag_id),
    FOREIGN KEY(source_id) REFERENCES communication_sources (id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
);

CREATE INDEX ix_communication_source_tags_tag_id ON communication_source_tags (tag_id);

