"""Offline contract tests and opt-in real YDB tests in isolated test tables."""
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import pytest

from pacemaker_registry import db, ydb_storage as storage
from pacemaker_registry.rating import default_event_rating, PaceRatingConfig
from pacemaker_registry.russiarunning import RaceResult, Checkpoint


def test_backend_dispatch_does_not_change_arguments(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "ydb")
    calls = []
    monkeypatch.setattr(storage, "load_registry", lambda *a, **kw: calls.append((a, kw)) or "registry")
    assert db.load_registry(4, include_splits=False, min_events=2) == "registry"
    assert calls == [((4,), {"include_splits": False, "min_events": 2})]


def test_unknown_backend_fails_closed(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "typo")
    with pytest.raises(RuntimeError, match="Unknown database backend"):
        db.check_database()


def test_table_prefix_rejects_sql(monkeypatch):
    monkeypatch.setenv("YDB_TABLE_PREFIX", "`; DROP TABLE events; --")
    with pytest.raises(RuntimeError, match="Invalid YDB table"):
        storage.table("events")


def test_migration_requires_source_checksum(tmp_path):
    spec = importlib.util.spec_from_file_location("migration", Path(__file__).parents[1] / "ops/ydb_migration.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps([{"backup": "{}", "snapshot_bytes": 2, "snapshot_md5": "wrong"}]))
    with pytest.raises(ValueError, match="checksum"):
        migration.read_snapshot(path)


@pytest.fixture
def live_ydb(monkeypatch):
    if os.getenv("YDB_INTEGRATION") != "1":
        pytest.skip("Set YDB_INTEGRATION=1 and SDK credentials to run real database tests")
    storage.close_database()
    # A fresh, isolated set of table names. Never modify the application's tables.
    prefix = "test_migration_" + uuid4().hex + "_"
    monkeypatch.setenv("YDB_TABLE_PREFIX", prefix)
    monkeypatch.setenv("DATABASE_BACKEND", "ydb")
    storage.initialize_database()
    storage._transaction(lambda tx: [storage._upsert(tx, "id_counters", {"name": name, "next_id": 150})
                                      for name in ("pacemakers", "events", "race_results")])
    yield
    driver, _ = storage._connection()
    for name in reversed(tuple(storage.TABLE_COLUMNS)):
        driver.table_client.session().create().drop_table(
            os.environ['YDB_DATABASE'] + '/' + prefix + name)
    storage.close_database()


def _result():
    return RaceResult("Тестовый Пейсер", "isolated-test-event", str(uuid4()), "Тестовый забег",
                      "10", "00:49:00", "00:49", "04:54 /км",
                      (Checkpoint("5", "5", "24:30", "04:54"),
                       Checkpoint("10", "5", "49:00", "04:54")), "https://example.invalid/result")


def test_live_crud_and_duplicate_concurrency(live_ydb):
    result = _result()
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: db.save_race_result(result, "00:49"), range(2)))
    assert sorted(outcomes) == [False, True]
    assert db.check_database() is None
    events = db.list_events()
    assert len(events) == 1 and events[0].id == 150
    event_id = events[0].id
    assert db.get_event_target_time_type(result.source_event_id) == "sub_minute"
    registry = db.load_registry()
    assert len(registry.pacemakers) == 1 and registry.result_count == 1
    pacer = registry.pacemakers[0]
    assert pacer.id == 150 and pacer.rating == 10
    assert db.get_result_details(150).result == pacer.results[0]
    assert db.get_result_details(999) is None
    assert db.get_event(999) is None
    assert db.save_event_rating(999, default_event_rating("round")) is False
    assert db.update_result_target_time(150, "00:50", "stale") is False
    assert db.update_result_target_time(150, "00:50", "00:49") is True
    assert db.get_result_details(150).result.target_time == "00:50"
    assert db.save_event_rating(event_id, default_event_rating("round")) is True
    assert db.save_event_pace_rating(event_id, PaceRatingConfig()) is True
    assert db.get_event(event_id).rating_config == default_event_rating("round")
    # Same normalized athlete/event, different result: no new athlete/event,
    # and the type of the first saved flag remains unchanged.
    second = replace(result, athlete_name="тестовый пейсер", source_participant_id=str(uuid4()))
    assert db.save_race_result(second, "00:50") is True
    assert db.get_event(event_id).target_time_type == "sub_minute"
    assert len(db.load_registry().pacemakers) == 1
    assert db.load_registry(min_events=2).result_count == 2
    assert db.load_registry(min_events=3).result_count == 0
    assert db.load_registry(event_id=999).selected_event_id is None
    assert db.load_registry(event_id=event_id, include_splits=False).selected_event_id == event_id
    with pytest.raises(ValueError):
        db.save_race_result(replace(result, distance_km="-1"), "00:49")
    assert db.load_registry().result_count == 2


def test_live_imported_data_has_postgres_parity(live_ydb, monkeypatch):
    snapshot_path = os.getenv("YDB_TEST_SNAPSHOT")
    pg_url = os.getenv("YDB_TEST_POSTGRES_URL")
    if not snapshot_path or not pg_url:
        pytest.skip("Snapshot and isolated restored PostgreSQL required for full parity test")
    spec = importlib.util.spec_from_file_location("migration", Path(__file__).parents[1] / "ops/ydb_migration.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    snapshot = migration.read_snapshot(snapshot_path)
    report = migration.import_snapshot(snapshot)
    assert sum(item['rows'] for item in report.values()) == sum(len(rows) for rows in snapshot['tables'].values())
    monkeypatch.setenv("DATABASE_URL", pg_url)
    def compare(function, *args, **kwargs):
        monkeypatch.setenv("DATABASE_BACKEND", "postgresql")
        before = function(*args, **kwargs)
        monkeypatch.setenv("DATABASE_BACKEND", "ydb")
        after = function(*args, **kwargs)
        # PostgreSQL uses OS collation (different on macOS and cloud Linux).
        # The adapter uses Unicode ordering. Option ordering is not scoring.
        if isinstance(before, db.Registry):
            before = replace(before, events=tuple(sorted(before.events, key=lambda e: e.id)))
            after = replace(after, events=tuple(sorted(after.events, key=lambda e: e.id)))
        elif function is db.list_events:
            before, after = (tuple(sorted(value, key=lambda e: e.id)) for value in (before, after))
        assert before == after, "PostgreSQL and YDB application output differs"
    compare(db.list_events)
    for event in snapshot['tables']['events']:
        compare(db.get_event, event['id'])
        compare(db.get_event_target_time_type, event['source_event_id'])
        for include_splits in (False, True):
            compare(db.load_registry, event['id'], include_splits=include_splits)
    for minimum in (1, 2, 3, 10000):
        compare(db.load_registry, min_events=minimum)
    for result in snapshot['tables']['race_results']:
        compare(db.get_result_details, result['id'])
