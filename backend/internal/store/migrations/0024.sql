ALTER TABLE devices ADD COLUMN language VARCHAR(2) NOT NULL DEFAULT 'ru' CHECK (language IN ('ru','en','zh'));
