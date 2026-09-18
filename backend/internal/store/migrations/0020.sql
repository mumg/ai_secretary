CREATE TABLE component_statuses (
    id VARCHAR(128) NOT NULL,
    label VARCHAR(255) NOT NULL,
    component_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    message TEXT,
    metrics JSON DEFAULT '{}' NOT NULL,
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_component_statuses_expires_at ON component_statuses (expires_at);

