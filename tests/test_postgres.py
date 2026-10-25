"""Opt-in integration tests against a disposable LOCAL PostgreSQL database only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from pacemaker_registry import db, migrate
from pacemaker_registry.rating import EventRatingConfig, PaceRatingConfig
from pacemaker_registry.russiarunning import Checkpoint, RaceResult


@pytest.fixture
def database(monkeypatch):
    dsn = os.getenv('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Set TEST_DATABASE_URL to a disposable local test database')
    settings = conninfo_to_dict(dsn)
    name, host = settings.get('dbname', ''), settings.get('host', '')
    if not (name.startswith('test_') or name.endswith('_test')) or not (
            host in {'localhost', '127.0.0.1', '::1'} or host.startswith('/')):
        pytest.fail('Integration tests require a named local test database, never production.')
    schema = 'test_' + uuid4().hex
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

    def connect():
        return psycopg.connect(dsn, options=f'-csearch_path={schema}')

    monkeypatch.setattr(db, 'connect_database', connect)
    monkeypatch.setattr(migrate, 'connect_database', connect)
    try:
        yield connect
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def sample(**changes):
    result = RaceResult('Testov First', 'test-event', str(uuid4()), 'Тестовый забег',
                        '10', '00:49:55', '00:50', '05:00 /км',
                        (Checkpoint('5', '5', '25:00', '05:00'), Checkpoint('10', '5', '49:55', '04:59')),
                        'https://results.runc.run/event/test/result/1/')
    return replace(result, **changes)


def test_schema_can_adopt_existing_installation_without_data_loss(database):
    with database() as connection:
        connection.execute((migrate.MIGRATIONS_DIR / '0001_baseline.sql').read_text())
    first = sample()
    assert db.save_race_result(first, '00:50')
    assert migrate.migrate() == ['0001_baseline.sql']
    assert migrate.migrate() == []
    assert db.load_registry().result_count == 1


def test_readiness_rejects_unmigrated_database_without_changing_it(database):
    with pytest.raises(psycopg.errors.UndefinedTable):
        db.check_database()
    with database() as connection:
        assert connection.execute("SELECT to_regclass('schema_migrations')").fetchone() == (None,)
    migrate.migrate()
    db.check_database()


def test_duplicates_names_event_type_and_json_round_trip(database):
    migrate.migrate()
    first = sample()
    assert db.save_race_result(first, '00:49')
    assert not db.save_race_result(first, '00:49')
    assert db.save_race_result(sample(athlete_name=' testov first '), '00:50')
    registry = db.load_registry()
    assert len(registry.pacemakers) == registry.event_count == 1
    assert registry.result_count == 2
    assert db.get_event_target_time_type('test-event') == 'sub_minute'
    event = registry.events[0]
    assert db.save_event_rating(event.id, EventRatingConfig(left_score=6))
    assert db.save_event_pace_rating(event.id, PaceRatingConfig(tolerance=2))
    assert db.get_event(event.id).formula.left_score == 6
    assert db.get_event(event.id).pace_formula.tolerance == 2
    detail = db.get_result_details(registry.pacemakers[0].results[0].id)
    assert detail.result.checkpoints == tuple(map(asdict, first.checkpoints))
    assert detail.result.pace_markers


def test_edit_conflict_and_filter_history(database):
    migrate.migrate()
    assert db.save_race_result(sample(), '00:50')
    assert db.save_race_result(sample(source_event_id='second-event'), '00:50')
    registry = db.load_registry()
    result = registry.pacemakers[0].results[0]
    assert db.update_result_target_time(result.id, '0:49', '00:50')
    assert not db.update_result_target_time(result.id, '00:48', '00:50')
    assert db.get_result_details(result.id).result.target_time == '00:49'
    filtered = db.load_registry(result.event_id, min_events=2)
    assert len(filtered.pacemakers[0].results) == 1
    assert filtered.pacemakers[0].performance_count == 2
    assert not db.load_registry(min_events=3).pacemakers


@pytest.mark.parametrize('include_splits', [True, False])
def test_compact_cards_preserve_detail_scores_and_full_checkpoints(database, include_splits):
    migrate.migrate()
    assert db.save_race_result(sample(), '00:50')
    assert db.save_race_result(sample(source_event_id='other', checkpoints=()), '00:50')
    registry = db.load_registry(include_splits=include_splits)
    event = registry.events[0]
    assert db.save_event_rating(event.id, EventRatingConfig(left_score=6, right_good=10, right_bad=45))
    assert db.save_event_pace_rating(event.id, PaceRatingConfig(tolerance=2))
    for card in db.load_registry(include_splits=include_splits).pacemakers[0].results:
        detail = db.get_result_details(card.id, include_splits).result
        assert (card.rating, card.finish_rating, card.splits_rating) == (
            detail.rating, detail.finish_rating, detail.splits_rating)
        assert card.finish_difference_display == detail.finish_difference_display
        assert not hasattr(card, 'checkpoints')
        assert not hasattr(card, 'pace_markers')
        assert not hasattr(card, 'actual_pace')
        if card.event_id == event.id:
            assert detail.checkpoints == tuple(map(asdict, sample().checkpoints))
            assert len(detail.pace_markers) == 2
        else:
            assert card.splits_rating is None


def test_concurrent_duplicate_is_inserted_once(database):
    migrate.migrate()
    result = sample()
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(lambda _: db.save_race_result(result, '00:50'), range(4)))
    assert sum(created) == 1
    assert db.load_registry().result_count == 1


def test_failed_insert_rolls_back_athlete_and_event(database):
    migrate.migrate()
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        db.save_race_result(sample(source_participant_id='not-a-uuid'), '00:50')
    with database() as connection:
        for table in ['pacemakers', 'events', 'race_results']:
            assert connection.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone() == (0,)


def test_migration_failure_rolls_back_everything(database, tmp_path, monkeypatch):
    (tmp_path / '0001_failure.sql').write_text('CREATE TABLE must_rollback (id int); SELECT missing_column;')
    monkeypatch.setattr(migrate, 'MIGRATIONS_DIR', tmp_path)
    with pytest.raises(psycopg.errors.UndefinedColumn):
        migrate.migrate()
    with database() as connection:
        assert connection.execute("SELECT to_regclass('must_rollback'), to_regclass('schema_migrations')").fetchone() == (None, None)


def test_applied_migrations_are_immutable(database, tmp_path, monkeypatch):
    migrate.migrate()
    (tmp_path / '0001_baseline.sql').write_text('SELECT 1;')
    monkeypatch.setattr(migrate, 'MIGRATIONS_DIR', tmp_path)
    with pytest.raises(RuntimeError, match='has changed'):
        migrate.migrate()
