SET LOCAL lock_timeout = '5s';

ALTER TABLE events ADD COLUMN splits_enabled BOOLEAN NOT NULL DEFAULT TRUE;
