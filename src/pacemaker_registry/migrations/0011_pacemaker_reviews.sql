CREATE TABLE pacemaker_reviews (
    pacemaker_id BIGINT NOT NULL REFERENCES pacemakers(id) ON DELETE CASCADE,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    author_key TEXT NOT NULL,
    account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
    score SMALLINT NOT NULL CHECK (score BETWEEN 1 AND 5),
    comment TEXT CHECK (comment IS NULL OR (char_length(comment) BETWEEN 1 AND 1000 AND account_id IS NOT NULL)),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pacemaker_id, event_id, author_key)
);
