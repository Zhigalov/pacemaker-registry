CREATE TABLE support_requests (
    id BIGSERIAL PRIMARY KEY,
    body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 4000),
    contact_kind TEXT NOT NULL CHECK (contact_kind IN ('telegram', 'email')),
    contact TEXT NOT NULL CHECK (char_length(contact) BETWEEN 1 AND 254),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    telegram_sent_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    closed_by BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
    closed_by_login TEXT,
    resolution TEXT CHECK (char_length(resolution) BETWEEN 1 AND 2000),
    CHECK ((closed_at IS NULL AND closed_by_login IS NULL AND resolution IS NULL)
        OR (closed_at IS NOT NULL AND closed_by_login IS NOT NULL AND resolution IS NOT NULL))
);

CREATE INDEX support_requests_inbox_idx ON support_requests ((closed_at IS NOT NULL), id DESC);

CREATE TABLE support_images (
    request_id BIGINT NOT NULL REFERENCES support_requests(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL CHECK (position BETWEEN 1 AND 5),
    image BYTEA NOT NULL CHECK (octet_length(image) BETWEEN 1 AND 430080),
    PRIMARY KEY (request_id, position)
);
