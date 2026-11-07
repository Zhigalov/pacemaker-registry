SET LOCAL lock_timeout = '5s';

ALTER TABLE race_results
    DROP CONSTRAINT race_results_target_time_check,
    ADD CONSTRAINT race_results_target_time_check
        CHECK (target_time ~ '^[0-9]{1,2}:[0-5][0-9](:[0-5][0-9])?$');
