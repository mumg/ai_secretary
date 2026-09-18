CREATE TABLE system_settings (
    id SERIAL NOT NULL,
    payload JSON NOT NULL,
    firebase_credentials_encrypted TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE communication_sources (
    id VARCHAR(128) NOT NULL,
    label VARCHAR(255) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    enabled BOOLEAN NOT NULL,
    settings JSON NOT NULL,
    credential_encrypted TEXT,
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

