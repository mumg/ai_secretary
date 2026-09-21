-- Preconfigured sources keep only their identity, runtime state and user delta.
ALTER TABLE communication_sources ADD COLUMN preconfigured BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE communication_sources ADD COLUMN configuration_deleted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE communication_sources ALTER COLUMN label DROP NOT NULL;
ALTER TABLE communication_sources ALTER COLUMN source_type DROP NOT NULL;
ALTER TABLE communication_sources ALTER COLUMN enabled DROP NOT NULL;
ALTER TABLE communication_sources ADD COLUMN configuration_fingerprint TEXT NOT NULL DEFAULT '';
