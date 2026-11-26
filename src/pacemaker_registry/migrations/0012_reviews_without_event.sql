ALTER TABLE pacemaker_reviews
    DROP CONSTRAINT pacemaker_reviews_pkey,
    ALTER COLUMN event_id DROP NOT NULL,
    ADD CONSTRAINT pacemaker_reviews_author_unique
        UNIQUE NULLS NOT DISTINCT (pacemaker_id, event_id, author_key);
