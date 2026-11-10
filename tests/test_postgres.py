"""Opt-in integration tests against a disposable LOCAL PostgreSQL database only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from pacemaker_registry import auth, db, migrate
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
    assert migrate.migrate() == [
        '0001_baseline.sql', '0002_pacemaker_profiles.sql', '0003_profile_details.sql',
        '0004_accounts.sql', '0005_account_identity.sql', '0006_profile_link.sql', '0007_account_names.sql', '0008_roles.sql',
        '0009_flag_time_seconds.sql', '0010_event_splits_enabled.sql',
    ]
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
    assert db.list_events()[0].result_count == 1
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
    assert db.save_race_result(sample(source_event_id='other-event'), '00:50')
    assert sorted(e.result_count for e in db.list_events()) == [1, 2]


def test_event_team_rating_uses_each_result_and_current_event_settings(database):
    migrate.migrate()
    assert db.save_race_result(sample(chip_time='00:50:00'), '00:50')
    assert db.save_race_result(sample(
        athlete_name='Second Runner', chip_time='00:50:00',
        checkpoints=(Checkpoint('10', '10', '50:00', '06:00'),),
    ), '00:50')
    assert db.save_race_result(sample(distance_km='5', chip_time='00:25:00', checkpoints=()), '00:25')
    assert db.save_race_result(sample(source_event_id='other-event', chip_time='00:50:00'), '00:50')

    events = {event.id: event for event in db.list_events()}
    team = next(event for event in events.values() if event.result_count == 3)
    other = next(event for event in events.values() if event.result_count == 1)
    results = [result for pacemaker in db.load_registry(event_id=team.id).pacemakers
               for result in pacemaker.results]
    assert len({result.rating for result in results}) > 1
    assert team.participant_count == 2
    assert other.participant_count == 1
    assert team.team_rating == pytest.approx(sum(result.rating for result in results) / 3)
    assert other.team_rating == pytest.approx(10)

    assert db.save_event_pace_rating(team.id, None, splits_enabled=False)
    updated = {event.id: event for event in db.list_events()}
    assert updated[team.id].team_rating == pytest.approx(10)
    assert updated[other.id].team_rating == other.team_rating


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


def test_flag_seconds_survive_database_save_and_edit(database):
    migrate.migrate()
    assert db.save_race_result(sample(distance_km='5', chip_time='22:27.4', checkpoints=()), '00:22:30')
    assert db.get_event_target_time_type('test-event') is None
    card = db.load_registry().pacemakers[0].results[0]
    result = db.get_result_details(card.id).result
    assert card.target_time_display == result.target_time_display == '00:22:30'
    assert result.target_pace == '04:30 /км'
    assert result.finish_deviation == -3
    assert db.update_result_target_time(card.id, '01:12:30', '00:22:30')
    assert not db.update_result_target_time(card.id, '00:22:45', '00:22:30')
    assert db.get_result_details(card.id).result.target_time_display == '01:12:30'


@pytest.mark.parametrize('splits_enabled', [True, False])
def test_compact_cards_preserve_detail_scores_and_full_checkpoints(database, splits_enabled):
    migrate.migrate()
    assert db.save_race_result(sample(), '00:50')
    assert db.save_race_result(sample(source_event_id='other', checkpoints=()), '00:50')
    registry = db.load_registry()
    event = registry.events[0]
    assert db.save_event_rating(event.id, EventRatingConfig(left_score=6, right_good=10, right_bad=45))
    assert db.save_event_pace_rating(event.id, PaceRatingConfig(tolerance=2), splits_enabled=splits_enabled)
    for card in db.load_registry().pacemakers[0].results:
        detail = db.get_result_details(card.id).result
        assert (card.rating, card.finish_rating, card.splits_rating) == (
            detail.rating, detail.finish_rating, detail.splits_rating)
        assert card.finish_difference_display == detail.finish_difference_display
        assert not hasattr(card, 'checkpoints')
        assert not hasattr(card, 'pace_markers')
        assert not hasattr(card, 'actual_pace')
        if card.event_id == event.id:
            assert detail.checkpoints == tuple(map(asdict, sample().checkpoints))
            assert len(detail.pace_markers) == (2 if splits_enabled else 0)
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
    for path in migrate.MIGRATIONS_DIR.glob('*.sql'):
        (tmp_path / path.name).write_text(path.read_text())
    (tmp_path / '0001_baseline.sql').write_text('SELECT 1;')
    monkeypatch.setattr(migrate, 'MIGRATIONS_DIR', tmp_path)
    with pytest.raises(RuntimeError, match='has changed'):
        migrate.migrate()


def test_profile_save_conflict_and_reimport_preserve_fields(database):
    migrate.migrate()
    assert db.save_race_result(sample(), '00:50')
    pacemaker_id = db.load_registry().pacemakers[0].id
    profile = db.get_pacemaker_profile(pacemaker_id)
    assert profile.version == 0 and profile.contacts == profile.achievements == ()
    assert len(profile.results) == 1
    contacts = [{'type': 'telegram', 'value': 'https://t.me/pacer_one'}]
    avatar = f'avatars/{pacemaker_id}/' + 'a' * 32 + '.webp'
    assert db.save_pacemaker_profile(pacemaker_id, 0, 'Ведём вместе', contacts, ['pshp_graduate'], avatar)
    assert not db.save_pacemaker_profile(pacemaker_id, 0, 'Overwrite', [], [], None)
    assert db.save_race_result(sample(), '00:50')
    changed = db.get_pacemaker_profile(pacemaker_id)
    assert changed.about == 'Ведём вместе' and changed.contacts == tuple(contacts)
    assert changed.achievements == ('pshp_graduate',)
    assert changed.version == 1 and len(changed.results) == 2
    assert changed.avatar_key == db.load_registry().pacemakers[0].avatar_key == avatar
    assert db.get_result_details(changed.results[0].id).pacemaker_id == pacemaker_id
    assert changed.results[0].rating == db.get_result_details(changed.results[0].id).result.rating
    assert not db.get_pacemaker_profile(pacemaker_id, with_results=False).results


def test_profile_details_lookup_reuse_unique_login_and_atomic_conflict(database):
    migrate.migrate()
    assert db.save_race_result(sample(), '00:50')
    assert db.save_race_result(sample(athlete_name='Other Runner'), '00:50')
    ids = [p.id for p in db.load_registry().pacemakers]
    first, second = ids
    records = {'21.1': {'time': '01:20:00', 'year': 2025, 'url': 'https://example.org/race'}}
    assert db.save_pacemaker_profile(first, 0, 'Я' * 5000, [], [], None,
        login='test_pacer', city='Москва', club='Бегуны', personal_records=records)
    changed = db.get_pacemaker_profile(first)
    assert (changed.login, changed.city, changed.club, changed.personal_records) == (
        'test_pacer', 'Москва', 'Бегуны', records)
    assert changed.about == 'Я' * 5000 and changed.public_path == '/p/test_pacer'
    assert db.find_pacemaker_login('TEST_PACER') == first
    assert not db.save_pacemaker_profile(first, 0, '', [], [], None, city='Не создавать', club='Не создавать')
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.save_pacemaker_profile(second, 0, '', [], [], None,
            login='test_pacer', city='Откат города', club='Откат клуба')
    choices = db.get_profile_choices()
    assert 'Не создавать' not in choices['cities'] and 'Откат города' not in choices['cities']
    assert 'Не создавать' not in choices['clubs'] and 'Откат клуба' not in choices['clubs']
    assert db.get_pacemaker_profile(second).version == 0
    assert db.save_pacemaker_profile(second, 0, '', [], [], None, city='москва', club='бегуны')
    other = db.get_pacemaker_profile(second)
    assert (other.city, other.club) == ('Москва', 'Бегуны')
    with database() as connection:
        assert connection.execute('SELECT city_id, club_id FROM pacemakers WHERE id IN (%s,%s) ORDER BY id', ids).fetchall()[0] == connection.execute('SELECT city_id, club_id FROM pacemakers WHERE id=%s', (second,)).fetchone()
    assert len(choices['cities']) > 100


def test_previous_mottos_survive_profile_extension(database, tmp_path, monkeypatch):
    original = migrate.MIGRATIONS_DIR
    for name in ('0001_baseline.sql', '0002_pacemaker_profiles.sql'):
        (tmp_path / name).write_text((original / name).read_text())
    monkeypatch.setattr(migrate, 'MIGRATIONS_DIR', tmp_path)
    migrate.migrate()
    with database() as connection:
        pacer = connection.execute("INSERT INTO pacemakers(last_name,first_name,motto) VALUES ('Old','Pacer','Старый девиз') RETURNING id").fetchone()[0]
    (tmp_path / '0003_profile_details.sql').write_text((original / '0003_profile_details.sql').read_text())
    migrate.migrate()
    assert db.get_pacemaker_profile(pacer, with_results=False).about == 'Старый девиз'


def test_concurrent_login_claim_only_one_profile_and_one_city(database):
    migrate.migrate()
    for name in ('Runner One', 'Runner Two'):
        assert db.save_race_result(sample(athlete_name=name), '00:50')
    ids = [p.id for p in db.load_registry().pacemakers]
    def save(pacer):
        try:
            return db.save_pacemaker_profile(pacer, 0, '', [], [], None,
                login='concurrent_pacer', city='Новый город', club='Новый клуб')
        except psycopg.errors.UniqueViolation:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(save, ids)) == 1
    with database() as connection:
        assert connection.execute("SELECT count(*) FROM cities WHERE normalized_name='новый город'").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM running_clubs WHERE normalized_name='новый клуб'").fetchone()[0] == 1


def test_distance_filter_combines_with_event_history_and_recalculates_rating(database):
    from decimal import Decimal
    migrate.migrate()
    assert db.save_race_result(sample(athlete_name='First Pacer', distance_km='10', chip_time='00:50:00'), '00:50')
    assert db.save_race_result(sample(athlete_name='First Pacer', source_event_id='half', distance_km='21.100', chip_time='01:45:00'), '01:40')
    assert db.save_race_result(sample(athlete_name='Other Pacer', distance_km='10.000', chip_time='00:49:50'), '00:50')
    assert db.save_race_result(sample(athlete_name='Third Pacer', source_event_id='marathon', distance_km='42.2', chip_time='03:59:00'), '04:00')
    all_results = db.load_registry()
    first = next(p for p in all_results.pacemakers if p.full_name == 'First Pacer')
    assert first.rating < 10
    ten = db.load_registry(distance_km=Decimal('10.000'))
    assert ten.distances == ('10', '21.1', '42.2') and ten.selected_distance == '10'
    assert len(ten.pacemakers) == 2 and ten.result_count == 2
    selected = next(p for p in ten.pacemakers if p.id == first.id)
    assert selected.rating == 10 and selected.performance_count == 2
    assert selected.mean_finish_deviation == 0
    assert all(card.distance_km == '10' for p in ten.pacemakers for card in p.results)
    half = db.load_registry(distance_km=Decimal('21.1'), min_events=2)
    assert [p.id for p in half.pacemakers] == [first.id]
    half_event = half.pacemakers[0].results[0].event_id
    combined = db.load_registry(half_event, distance_km=Decimal('21.100'), min_events=2)
    assert combined.result_count == 1 and combined.selected_event_id == half_event
    empty = db.load_registry(half_event, distance_km=Decimal('10'))
    assert not empty.pacemakers and empty.result_count == empty.event_count == 0
    assert empty.distances == ten.distances and len(empty.events) == 3
    assert db.load_registry(distance_km=Decimal('7.777')).result_count == 0
    restored = db.load_registry()
    assert restored == all_results


def test_name_search_all_words_order_case_yo_and_other_filters(database):
    migrate.migrate()
    for name, event, distance in (
        ('Петров Иван', 'one', '10'), ('Петров Иван', 'two', '21.1'),
        ('Петров Пётр', 'one', '10'), ('Иванов Сергей', 'one', '10'),
        ('Сидоров Иван', 'one', '10'), ('Елкин Артём', 'one', '10'),
    ):
        assert db.save_race_result(sample(athlete_name=name, source_event_id=event, distance_km=distance), '00:50')
    def names(query, **filters):
        return {p.full_name for p in db.load_registry(search=query, **filters).pacemakers}
    assert names('иван') == {'Петров Иван', 'Иванов Сергей', 'Сидоров Иван'}
    assert names('ИВАН ПЕТРОВ') == names('  Петров\t\nИван  ') == {'Петров Иван'}
    assert names('Ив петр') == {'Петров Иван'}
    assert names('ПЕТР') == {'Петров Иван', 'Петров Пётр'}
    assert names('петр петров') == {'Петров Пётр'}
    assert names('ёЛКИН АРТЕМ') == {'Елкин Артём'}
    from decimal import Decimal
    filtered = db.load_registry(search='Иван Петров', distance_km=Decimal('10'), min_events=2)
    assert len(filtered.pacemakers) == 1 and filtered.result_count == 1
    assert filtered.pacemakers[0].performance_count == 2
    event = filtered.pacemakers[0].results[0].event_id
    assert names('Петров Иван', event_id=event, distance_km=Decimal('21.1')) == set()
    assert filtered.distances == ('10', '21.1') and len(filtered.events) == 2
    for query in ('%', '_', "' OR 1=1 --", 'Неизвестный'):
        assert names(query) == set()  # No LIKE wildcards or SQL interpolation.
    assert names('   ') == names('')
    with database() as connection:
        assert connection.execute("SELECT first_name FROM pacemakers WHERE last_name='Елкин'").fetchone()[0] == 'Артём'


def test_account_sessions_are_independent_and_reusable(database):
    from hashlib import sha256
    from starlette.requests import Request

    def request(token):
        return Request({"type": "http", "headers": [
            (b"cookie", f"{auth.SESSION_COOKIE}={token}".encode()),
        ]})

    migrate.migrate()
    identity = auth.YandexIdentity("12345", "sample.login", "123/avatar-test")
    with ThreadPoolExecutor(max_workers=2) as pool:
        phone, laptop = list(pool.map(lambda _: auth.create_session(identity, None), range(2)))
    account = auth.current_account(request(phone))
    assert account == auth.SignedInAccount(account.id, identity.login, identity.avatar_id)
    assert auth.current_account(request(laptop)) == account
    assert auth.current_account(request("unknown")) is None

    auth.end_session(request(phone))
    assert auth.current_account(request(phone)) is None
    assert auth.current_account(request(laptop)) == account

    with database() as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM pacemakers").fetchone() == (0,)
        assert connection.execute("SELECT token_hash FROM account_sessions").fetchone() == (
            sha256(laptop.encode()).digest(),
        )
        connection.execute("UPDATE account_sessions SET expires_at = now()")
    assert auth.current_account(request(laptop)) is None


def test_profile_onboarding_finds_and_claims_only_unlinked_exact_names(database, monkeypatch):
    from fastapi.testclient import TestClient
    from pacemaker_registry.main import app

    migrate.migrate()
    assert db.save_race_result(sample(athlete_name='Семёнов Артём'), '00:50')
    pacemaker_id = db.load_registry().pacemakers[0].id
    with database() as connection:
        another = connection.execute(
            "INSERT INTO pacemakers (last_name, first_name) VALUES ('Семенов', 'Артемий') RETURNING id"
        ).fetchone()[0]
    monkeypatch.setenv('YANDEX_OAUTH_CLIENT_ID', 'test-client')
    origin = 'https://pacersreg.ru'
    path = '/account/profile'
    names = {'last_name': '  СЕМЕНОВ ', 'first_name': 'артем'}
    token = auth.create_session(auth.YandexIdentity('111', 'test.pacer', last_name='Семёнов', first_name='Артём'), None)
    other_token = auth.create_session(auth.YandexIdentity('222', 'second.pacer'), None)
    with TestClient(app, base_url=origin, follow_redirects=False) as client:
        assert client.get(path).headers['location'] == '/auth/yandex'
        assert client.post(path, data=dict(names, action='search'), headers={'Origin': origin}).status_code == 403
        client.cookies.set(auth.SESSION_COOKIE, token)
        setup = client.get(path)
        assert setup.status_code == 200
        assert 'name="last_name" value="Семёнов"' in setup.text
        assert 'name="first_name" value="Артём"' in setup.text
        assert client.post(path, data=dict(names, action='search'), headers={'Origin': 'https://example.test'}).status_code == 403
        candidates = client.post(path, data=dict(names, action='search'), headers={'Origin': origin})
        assert f'name="candidate_id" value="{pacemaker_id}"' in candidates.text
        assert f'name="candidate_id" value="{another}"' not in candidates.text
        invalid = client.post(path, data=dict(names, action='link', candidate_id=another), headers={'Origin': origin})
        assert invalid.status_code == 422
        result = client.post(path, data=dict(names, action='link', candidate_id=pacemaker_id), headers={'Origin': origin})
        assert result.headers['location'] == f'/pacemakers/{pacemaker_id}'
        assert client.get(path).headers['location'] == result.headers['location']
        client.cookies.set(auth.SESSION_COOKIE, other_token)
        blocked = client.post(path, data=dict(names, action='link', candidate_id=pacemaker_id), headers={'Origin': origin})
        assert blocked.status_code == 422
        assert 'уже связан' in blocked.text
    assert len(db.get_pacemaker_profile(pacemaker_id).results) == 1


@pytest.mark.parametrize('publish_email', [False, True])
def test_profile_onboarding_creates_with_optional_identity_details(database, monkeypatch, publish_email):
    from fastapi.testclient import TestClient
    from pacemaker_registry.main import app

    migrate.migrate()
    monkeypatch.setenv('YANDEX_OAUTH_CLIENT_ID', 'test-client')
    origin = 'https://pacersreg.ru'
    identity = auth.YandexIdentity('1234', 'new.pacer', '123/image', 'test@example.com')
    token = auth.create_session(identity, None)
    with database() as connection:
        connection.execute(
            "INSERT INTO pacemakers(last_name, first_name, login) VALUES ('Другое', 'Имя', 'new-pacer')"
        )
    with TestClient(app, base_url=origin, follow_redirects=False) as client:
        client.cookies.set(auth.SESSION_COOKIE, token)
        fields = {'last_name': 'Тестов', 'first_name': 'Новый'}
        headers = {'Origin': origin}
        found = client.post('/account/profile', data=dict(fields, action='search'), headers=headers)
        assert 'value="new-pacer"' in found.text
        assert 'name="publish_email" value="true" checked' not in found.text
        assert 'test@example.com' in found.text
        assert found.headers['cache-control'] == 'private, no-store'
        details = dict(fields, action='create', publish_email=str(publish_email).lower(), use_photo='true')
        duplicate = client.post('/account/profile', data=dict(details, login='new-pacer'), headers=headers)
        assert duplicate.status_code == 422
        assert 'логин уже занят' in duplicate.text
        created = client.post('/account/profile', data=dict(details, login='new-pacer-2'), headers=headers)
        assert created.status_code == 303
        profile = db.get_pacemaker_profile(int(created.headers['location'].split('/')[-1]))
        assert profile.login == 'new-pacer-2'
        assert profile.avatar_key == 'yandex/123/image'
        assert bool(profile.contacts) is publish_email
        if publish_email:
            assert profile.contacts[0]['value'] == 'test@example.com'
        page = client.get(created.headers['location'])
        assert 'https://avatars.yandex.net/get-yapic/123/image/islands-200' in page.text
        assert ('test@example.com' in page.text) is publish_email


def test_first_successful_import_assigns_only_one_captain(database):
    migrate.migrate()
    with database() as connection:
        account_ids = [row[0] for row in connection.execute(
            "INSERT INTO accounts (yandex_user_id) VALUES ('111'), ('222') RETURNING id"
        )]
    assert db.save_race_result(sample(source_event_id='existing'), '00:50')
    assert db.save_race_result(sample(source_event_id='existing'), '00:50', account_ids[0])
    existing = next(e for e in db.list_events() if e.id == 1)
    assert db.get_event(existing.id).captain_account_id is None

    first_result, second_result = (sample(athlete_name=name) for name in ('First Runner', 'Second Runner'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(db.save_race_result, first_result, '00:50', account_ids[0])
        second = pool.submit(db.save_race_result, second_result, '00:50', account_ids[1])
        assert first.result() and second.result()
    with database() as connection:
        first_insert = connection.execute("""
            SELECT e.captain_account_id, rr.source_participant_id::text
            FROM race_results rr JOIN events e ON e.id = rr.event_id
            WHERE e.source_event_id = %s ORDER BY rr.id LIMIT 1
        """, (first_result.source_event_id,)).fetchone()
    expected = {first_result.source_participant_id: account_ids[0], second_result.source_participant_id: account_ids[1]}
    assert first_insert[0] == expected[first_insert[1]]
    assert not db.save_race_result(first_result, '00:50', account_ids[1])
    listed = next(e for e in db.list_events() if e.id != existing.id)
    assert listed.result_count == 2
    assert db.get_event(listed.id).captain_account_id == first_insert[0]


def test_admin_role_management_and_protected_achievement(database, monkeypatch):
    from fastapi.testclient import TestClient
    from pacemaker_registry.main import app

    migrate.migrate()
    monkeypatch.setenv('YANDEX_OAUTH_CLIENT_ID', 'test-client')
    origin = 'https://pacersreg.ru'
    headers = {'Origin': origin}
    admin_token = auth.create_session(auth.YandexIdentity('101', 'admin'), None)
    owner_token = auth.create_session(auth.YandexIdentity('102', 'owner'), None)
    assert db.save_race_result(sample(), '00:50')
    with database() as connection:
        admin_id = connection.execute(
            "UPDATE accounts SET is_admin = true WHERE yandex_user_id = '101' RETURNING id"
        ).fetchone()[0]
        owner_id = connection.execute("SELECT id FROM accounts WHERE yandex_user_id = '102'").fetchone()[0]
        profile_id = connection.execute(
            "UPDATE pacemakers SET account_id = %s, achievements = '[\"pshp_graduate\"]' RETURNING id",
            (owner_id,),
        ).fetchone()[0]
        event_id = connection.execute("SELECT id FROM events").fetchone()[0]
    with TestClient(app, base_url=origin, follow_redirects=False) as client:
        client.cookies.set(auth.SESSION_COOKIE, owner_token)
        path = f'/pacemakers/{profile_id}/edit'
        edit = client.get(path)
        assert edit.status_code == 200 and 'name="achievements"' not in edit.text
        assert client.post(path, data={'profile_version': 0, 'about': 'О себе'}, headers=headers).status_code == 303
        assert db.get_pacemaker_profile(profile_id).achievements == ('pshp_graduate',)
        assert client.post('/admin/roles', data={'account_id': owner_id, 'make_admin': 'true'}, headers=headers).status_code == 403
        client.cookies.set(auth.SESSION_COOKIE, admin_token)
        assert client.get('/admin/roles').status_code == 200
        assert client.post('/admin/roles', data={'account_id': admin_id, 'make_admin': 'false'}, headers=headers).status_code == 409
        assert client.post('/admin/roles', data={'account_id': owner_id, 'event_id': event_id}, headers=headers).status_code == 303
        assert db.get_event(event_id).captain_account_id == owner_id
        assert client.post(path, data={'profile_version': 1}, headers=headers).status_code == 303
        assert db.get_pacemaker_profile(profile_id).achievements == ()
        assert client.post('/admin/roles', data={'account_id': owner_id, 'make_admin': 'true'}, headers=headers).status_code == 303
        client.cookies.set(auth.SESSION_COOKIE, owner_token)
        assert client.get('/admin/roles').status_code == 200
