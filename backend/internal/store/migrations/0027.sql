CREATE TABLE setup_wizard_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    completed BOOLEAN NOT NULL DEFAULT false,
    llm_verified BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
