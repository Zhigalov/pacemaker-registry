ALTER TABLE pacemakers
    ADD COLUMN motto TEXT CHECK (char_length(motto) <= 200),
    ADD COLUMN avatar_key TEXT,
    ADD COLUMN contacts JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(contacts) = 'array'),
    ADD COLUMN achievements JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(achievements) = 'array'),
    ADD COLUMN profile_version INTEGER NOT NULL DEFAULT 0 CHECK (profile_version >= 0),
    ADD COLUMN profile_updated_at TIMESTAMPTZ;
