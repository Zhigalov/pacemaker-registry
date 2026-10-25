CREATE TABLE IF NOT EXISTS pacemakers (
    id BIGSERIAL PRIMARY KEY,
    last_name TEXT NOT NULL CHECK (btrim(last_name) <> ''),
    first_name TEXT NOT NULL CHECK (btrim(first_name) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS pacemakers_name_unique
    ON pacemakers ((lower(btrim(last_name))), (lower(btrim(first_name))));

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    source_event_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    target_time_type TEXT CHECK (
        target_time_type IN ('round', 'sub_minute')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS race_results (
    id BIGSERIAL PRIMARY KEY,
    pacemaker_id BIGINT NOT NULL REFERENCES pacemakers(id),
    event_id BIGINT NOT NULL REFERENCES events(id),
    source_participant_id UUID NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    distance_km NUMERIC(8, 3) NOT NULL CHECK (distance_km > 0),
    chip_time TEXT NOT NULL,
    pace TEXT NOT NULL,
    target_time TEXT NOT NULL CHECK (target_time ~ '^[0-9]{1,2}:[0-5][0-9]$'),
    checkpoints JSONB NOT NULL CHECK (jsonb_typeof(checkpoints) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE events ADD COLUMN IF NOT EXISTS rating_config JSONB
    CHECK (rating_config IS NULL OR jsonb_typeof(rating_config) = 'object');

ALTER TABLE events ADD COLUMN IF NOT EXISTS pace_rating_config JSONB
    CHECK (pace_rating_config IS NULL OR jsonb_typeof(pace_rating_config) = 'object');

CREATE INDEX IF NOT EXISTS race_results_pacemaker_id_idx
    ON race_results (pacemaker_id);

CREATE INDEX IF NOT EXISTS race_results_event_id_idx
    ON race_results (event_id);

ALTER TABLE events
    ADD COLUMN IF NOT EXISTS target_time_type TEXT CHECK (
        target_time_type IN ('round', 'sub_minute')
    );

UPDATE events AS event
SET target_time_type = (
    SELECT CASE
        WHEN split_part(result.target_time, ':', 2)::INTEGER % 5 = 4
            THEN 'sub_minute'
        ELSE 'round'
    END
    FROM race_results AS result
    WHERE result.event_id = event.id
    ORDER BY result.created_at, result.id
    LIMIT 1
)
WHERE event.target_time_type IS NULL
  AND EXISTS (
      SELECT 1
      FROM race_results AS result
      WHERE result.event_id = event.id
  );
