ALTER TABLE pacemakers ADD COLUMN donation_url text
    CHECK (donation_url ~ '^https://pay[.]cloudtips[.]ru/p/[A-Za-z0-9_-]{1,64}$');
