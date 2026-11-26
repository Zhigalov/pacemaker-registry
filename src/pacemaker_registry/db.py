import os
from collections import defaultdict
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row

from pacemaker_registry.imports import TARGET_TIME_TYPE_ROUND, TARGET_TIME_TYPE_SUB_MINUTE, RaceResult, ResultImportError


from pacemaker_registry.rating import parse_event_rating, parse_pace_rating, EventRatingConfig, PaceRatingConfig, mean_rating, median_rating, pacemaker_rating
from pacemaker_registry.names import matches_name
from pacemaker_registry.models import Registry, RegistryEvent, RegistryPacemaker, ResultDetails, PacemakerProfile
from pacemaker_registry.registry import score_result, _sort_pacemakers
from pacemaker_registry.times import normalize_target_time, target_seconds, parse_distance


def _read_rows(connection, query, parameters=()):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, parameters).fetchall()


def _read_results(connection, conditions=(), parameters=(), *, events=None, details=False,
                  order="lower(p.last_name), lower(p.first_name), rr.created_at DESC, lower(e.name)"):
    """Load and score the same result fields for every application view."""
    checkpoints = "rr.checkpoints" if details else """CASE WHEN e.splits_enabled THEN COALESCE((
        SELECT jsonb_agg(jsonb_build_object('segment_distance_km', point->'segment_distance_km',
                                           'pace_per_km', point->'pace_per_km'))
        FROM jsonb_array_elements(rr.checkpoints) AS point), '[]'::jsonb) ELSE '[]'::jsonb END"""
    event_fields = "" if events is not None else """, e.name AS event_name, e.target_time_type,
        e.rating_config, e.pace_rating_config, e.splits_enabled, e.captain_account_id"""
    detail_fields = ", rr.pace AS actual_pace, rr.source_url" if details else ""
    query = f"""SELECT rr.id, rr.distance_km, rr.target_time, rr.chip_time,
        {checkpoints} AS checkpoints,
        p.id AS pacemaker_id, p.last_name, p.first_name, p.avatar_key, history.total_result_count,
        e.id AS event_id {event_fields} {detail_fields}
        FROM race_results rr JOIN events e ON e.id = rr.event_id
        JOIN pacemakers p ON p.id = rr.pacemaker_id
        JOIN (SELECT pacemaker_id, count(*) AS total_result_count FROM race_results GROUP BY pacemaker_id)
            history ON history.pacemaker_id = p.id"""
    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    query += ' ORDER BY ' + order
    events = {} if events is None else events
    rows = _read_rows(connection, query, parameters)
    results = []
    for row in rows:
        event_id = row['event_id']
        if event_id not in events:
            events[event_id] = _event_from_row(dict(row, id=event_id, name=row['event_name']))
        results.append(score_result(
            row['id'], events[event_id], row['distance_km'], row['target_time'], row['chip_time'],
            row.get('actual_pace'), row['checkpoints'], details=details, source_url=row.get('source_url', ''),
            **{key: row[key] for key in ('pacemaker_id', 'last_name', 'first_name',
                                        'avatar_key', 'total_result_count')},
        ))
    return results


def get_result_details(result_id: int) -> ResultDetails | None:
    with connect_database() as connection:
        results = _read_results(connection, ('rr.id = %s',), (result_id,), details=True)
    if not results:
        return None
    result = results[0]
    return ResultDetails(f"{result.last_name} {result.first_name}", result.source_url,
                         result.event, result.details(), result.pacemaker_id)


def update_result_target_time(result_id: int, target_time: str, previous_target_time: str) -> bool:
    """Change only the flag; refuse to overwrite a concurrently edited result."""
    target_time = normalize_target_time(target_time)
    with connect_database() as connection:
        return connection.execute(
            """UPDATE race_results SET target_time = %s
               WHERE id = %s AND target_time = %s RETURNING id""",
            (target_time, result_id, previous_target_time),
        ).fetchone() is not None


def connect_database() -> Connection[Any]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url, connect_timeout=5)

    settings = {
        "host": os.getenv("DATABASE_HOST"),
        "port": os.getenv("DATABASE_PORT", "6432"),
        "dbname": os.getenv("DATABASE_NAME"),
        "user": os.getenv("DATABASE_USER"),
        "password": os.getenv("DATABASE_PASSWORD"),
        "sslmode": os.getenv("DATABASE_SSLMODE", "disable"),
        "target_session_attrs": "read-write",
        "connect_timeout": 5,
    }
    missing = [
        key for key in ("host", "dbname", "user", "password") if not settings[key]
    ]
    if missing:
        raise RuntimeError(
            "Database connection is not configured: " + ", ".join(missing)
        )
    return psycopg.connect(**settings)


def check_database() -> None:
    """A reachable server without the application's schema is not ready."""
    with connect_database() as connection:
        connection.execute("""SELECT rr.id, e.rating_config, e.pace_rating_config, e.splits_enabled, p.last_name,
            p.motto, p.avatar_key, p.contacts, p.achievements, p.profile_version, p.profile_updated_at,
            p.login, p.city_id, p.club_id, p.personal_records, p.account_id, e.captain_account_id
            FROM race_results AS rr
            JOIN events AS e ON e.id = rr.event_id
            JOIN pacemakers AS p ON p.id = rr.pacemaker_id
            LIMIT 0""")
        connection.execute("""SELECT a.yandex_user_id, a.yandex_login, a.yandex_avatar_id, a.yandex_email,
            a.yandex_last_name, a.yandex_first_name, a.is_admin, s.token_hash, s.expires_at
            FROM account_sessions AS s JOIN accounts AS a ON a.id = s.account_id
            LIMIT 0""")
        connection.execute("SELECT score, comment FROM pacemaker_reviews LIMIT 0")


def get_pacemaker_profile(pacemaker_id: int, *,
                          with_results: bool = True, rating_formula=mean_rating,
                          include_rank: bool = False) -> PacemakerProfile | None:
    with connect_database() as connection:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        rows = _read_rows(connection,
            """SELECT p.id, p.last_name, p.first_name, p.motto AS about, p.avatar_key, p.contacts,
                      p.achievements, p.profile_version AS version, p.login, c.name AS city,
                      bc.name AS club, p.personal_records
               FROM pacemakers p LEFT JOIN cities c ON c.id = p.city_id
               LEFT JOIN running_clubs bc ON bc.id = p.club_id WHERE p.id = %s""", (pacemaker_id,),
        )
        if not rows:
            return None
        row = rows[0]
        results = []
        rank = None
        if with_results:
            results = _read_results(
                connection, ('p.id = %s',), (pacemaker_id,), order="rr.created_at DESC, rr.id DESC",
            )
        if include_rank and results:
            ranked = _rank_pacemakers(_read_results(connection), rating_formula)
            rank = next(position for position, pacer in enumerate(ranked, 1) if pacer.id == pacemaker_id)
    return PacemakerProfile(**dict(row, contacts=tuple(row['contacts']), achievements=tuple(row['achievements'])),
                            results=tuple(result.card() for result in results),
                            rating=pacemaker_rating(results, rating_formula), rank=rank)


def get_profile_choices() -> dict:
    with connect_database() as connection:
        return {key: [r[0] for r in connection.execute(f'SELECT name FROM {table} ORDER BY name').fetchall()]
                for key, table in (('cities', 'cities'), ('clubs', 'running_clubs'))}


def find_pacemaker_login(login: str) -> int | None:
    with connect_database() as connection:
        row = connection.execute('SELECT id FROM pacemakers WHERE login = %s', (login.lower(),)).fetchone()
    return row[0] if row else None


def _profile_choice(connection, table: str, name: str) -> int | None:
    if not name:
        return None
    # One key for case, ё/е and repeated whitespace. Unique constraint handles concurrent inserts.
    key = name.casefold().replace('ё', 'е')
    return connection.execute(f"""INSERT INTO {table} (name, normalized_name) VALUES (%s, %s)
        ON CONFLICT (normalized_name) DO UPDATE SET normalized_name = EXCLUDED.normalized_name
        RETURNING id""", (name, key)).fetchone()[0]


def save_pacemaker_profile(pacemaker_id: int, version: int, about: str,
                           contacts: list[dict], achievements: list[str], avatar_key: str | None,
                           *, login=None, city='', club='', personal_records=None) -> bool:
    with connect_database() as connection:
        # Lock before creating lookup values: stale edits must not leave orphan cities or clubs.
        row = connection.execute('SELECT profile_version FROM pacemakers WHERE id = %s FOR UPDATE',
                                 (pacemaker_id,)).fetchone()
        if row is None or row[0] != version:
            return False
        city_id = _profile_choice(connection, 'cities', city)
        club_id = _profile_choice(connection, 'running_clubs', club)
        connection.execute(
            """UPDATE pacemakers SET motto = %s, contacts = %s, achievements = %s, avatar_key = %s,
                   login = %s, city_id = %s, club_id = %s, personal_records = %s,
                   profile_version = profile_version + 1, profile_updated_at = now()
               WHERE id = %s""",
            (about or None, Jsonb(contacts), Jsonb(achievements), avatar_key, login, city_id, club_id,
             Jsonb(personal_records or {}), pacemaker_id))
        return True


def get_event_target_time_type(source_event_id: str) -> str | None:
    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT target_time_type
            FROM events
            WHERE source_event_id = %s
            """,
            (source_event_id,),
        ).fetchone()
    return row[0] if row else None


REGISTRY_PAGE_SIZE = 25


def _rank_pacemakers(results, rating_formula):
    grouped = defaultdict(list)
    for result in results:
        grouped[result.pacemaker_id].append(result)
    pacemakers = []
    for pid, performances in grouped.items():
        first = performances[0]
        pacemakers.append(RegistryPacemaker(
            pid, f"{first.last_name} {first.first_name}", first.last_name[0] + first.first_name[0],
            pacemaker_rating(performances, rating_formula), tuple(performances),
            first.total_result_count, first.avatar_key,
        ))
    return _sort_pacemakers(pacemakers, filtered_count=rating_formula is median_rating)


def load_registry(
    event_id: int | None = None,
    min_events: int = 1,
    distance_km: Decimal | None = None,
    search: str = "",
    page: int | None = None,
    page_size: int = REGISTRY_PAGE_SIZE,
    rating_formula=mean_rating,
) -> Registry:
    with connect_database() as connection:
        # All queries must see the same results and formulas during concurrent saves.
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        event_rows = _read_rows(connection,
            """
            SELECT e.id, e.name, e.target_time_type, e.rating_config, e.pace_rating_config, e.splits_enabled
            FROM events AS e
            WHERE EXISTS (
                SELECT 1
                FROM race_results AS rr
                WHERE rr.event_id = e.id
            )
            ORDER BY lower(e.name), e.id
            """
        )
        events = tuple(_event_from_row(row) for row in event_rows)
        events_by_id = {event.id: event for event in events}
        selected_event_id = (
            event_id if event_id in {event.id for event in events} else None
        )

        distances = tuple(format(row[0].normalize(), 'f') for row in connection.execute(
            'SELECT DISTINCT distance_km FROM race_results ORDER BY distance_km').fetchall())
        selected_distance = format(distance_km.normalize(), 'f') if distance_km else None

        conditions, parameters = ["history.total_result_count >= %s"], (min_events,)
        if selected_event_id is not None:
            conditions.append("e.id = %s")
            parameters += (selected_event_id,)
        if distance_km:
            conditions.append('rr.distance_km = %s')
            parameters += (distance_km,)
        search = ' '.join(search.split())
        if search:
            names = _read_rows(connection, 'SELECT id, last_name, first_name FROM pacemakers')
            conditions.append('p.id = ANY(%s)')
            parameters += ([p['id'] for p in names if matches_name(f"{p['last_name']} {p['first_name']}", search)],)
        results = _read_results(connection, conditions, parameters, events=events_by_id)

    pacemakers = _rank_pacemakers(results, rating_formula)
    count = len(pacemakers)
    page_count = max(1, (count + page_size - 1) // page_size)
    if page is not None:
        page = min(max(1, page), page_count)
        pacemakers = pacemakers[(page - 1) * page_size:page * page_size]
    return Registry(
        pacemakers=tuple(replace(pacer, results=tuple(result.card() for result in pacer.results))
                         for pacer in pacemakers),
        event_count=len({result.event.id for result in results}), result_count=len(results),
        events=events, selected_event_id=selected_event_id, min_events=min_events,
        distances=distances, selected_distance=selected_distance, search=search,
        pacemaker_count=count, page=page or 1, page_count=page_count,
    )


def save_race_result(result: RaceResult, target_time: str, account_id: int | None = None) -> bool:
    target_time = normalize_target_time(target_time)
    last_name, first_name = _split_athlete_name(result.athlete_name)
    checkpoints = [asdict(checkpoint) for checkpoint in result.checkpoints]
    distance = parse_distance(result.distance_km)
    target_time_type = _target_time_type_from_target_time(target_time)

    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pacemakers (last_name, first_name)
                VALUES (%s, %s)
                ON CONFLICT ((lower(btrim(last_name))), (lower(btrim(first_name))))
                DO UPDATE SET
                    last_name = EXCLUDED.last_name,
                    first_name = EXCLUDED.first_name
                RETURNING id
                """,
                (last_name, first_name),
            )
            pacemaker_id = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO events (source_event_id, name, target_time_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (source_event_id)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    target_time_type = COALESCE(
                        events.target_time_type,
                        EXCLUDED.target_time_type
                    )
                RETURNING id
                """,
                (result.source_event_id, result.event_name, target_time_type),
            )
            event_id = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO race_results (
                    pacemaker_id,
                    event_id,
                    source_participant_id,
                    source_url,
                    distance_km,
                    chip_time,
                    pace,
                    target_time,
                    checkpoints
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_participant_id) DO NOTHING
                RETURNING id
                """,
                (
                    pacemaker_id,
                    event_id,
                    result.source_participant_id,
                    result.source_url,
                    distance,
                    result.chip_time,
                    result.pace,
                    target_time,
                    Jsonb(checkpoints),
                ),
            )
            inserted = cursor.fetchone()
            if inserted and account_id is not None:
                # The event upsert holds the row lock until commit, serializing first imports.
                cursor.execute(
                    """UPDATE events SET captain_account_id = %s
                       WHERE id = %s AND captain_account_id IS NULL
                       AND NOT EXISTS (
                           SELECT 1 FROM race_results WHERE event_id = %s AND id <> %s
                       )""",
                    (account_id, event_id, event_id, inserted[0]),
                )
            return inserted is not None


def _target_time_type_from_target_time(target_time: str) -> str | None:
    minutes, seconds = divmod(target_seconds(target_time), 60)
    if seconds:
        return None
    if minutes % 5 == 4:
        return TARGET_TIME_TYPE_SUB_MINUTE
    return TARGET_TIME_TYPE_ROUND


def _split_athlete_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split(maxsplit=1)
    if len(parts) != 2:
        raise ResultImportError("Не удалось разделить фамилию и имя участника.")
    return parts[0], parts[1]


def _event_from_row(row: dict) -> RegistryEvent:
    return RegistryEvent(
        id=row['id'], name=row['name'], target_time_type=row['target_time_type'],
        rating_config=parse_event_rating(row['rating_config']) if row['rating_config'] is not None else None,
        pace_rating_config=parse_pace_rating(row['pace_rating_config']) if row['pace_rating_config'] is not None else None,
        captain_account_id=row.get('captain_account_id'), splits_enabled=row['splits_enabled'],
    )


def get_event(event_id: int) -> RegistryEvent | None:
    with connect_database() as connection:
        rows = _read_rows(connection,
            "SELECT id, name, target_time_type, rating_config, pace_rating_config, splits_enabled, captain_account_id FROM events WHERE id = %s",
            (event_id,),
        )
    return _event_from_row(rows[0]) if rows else None


def list_events() -> tuple[RegistryEvent, ...]:
    with connect_database() as connection:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        event_rows = _read_rows(connection,
            """SELECT e.id, e.name, e.target_time_type, e.rating_config, e.pace_rating_config, e.splits_enabled
               FROM events AS e
               ORDER BY lower(e.name), e.id"""
        )
        events = {row['id']: _event_from_row(row) for row in event_rows}
        results = _read_results(connection, events=events)

    grouped = defaultdict(list)
    for result in results:
        grouped[result.event.id].append(result)
    return tuple(replace(event, result_count=len(grouped[event.id]), team_rating=mean_rating(grouped[event.id]),
                         participant_count=len({result.pacemaker_id for result in grouped[event.id]}))
                 for event in events.values())


def save_event_rating(event_id: int, config: EventRatingConfig) -> bool:
    with connect_database() as connection:
        row = connection.execute(
            "UPDATE events SET rating_config = %s WHERE id = %s RETURNING id",
            (Jsonb(asdict(config)), event_id),
        ).fetchone()
    return row is not None


def save_event_pace_rating(event_id: int, config: PaceRatingConfig | None, *,
                           splits_enabled: bool | None = None) -> bool:
    with connect_database() as connection:
        row = connection.execute(
            """UPDATE events SET pace_rating_config = COALESCE(%s, pace_rating_config),
                                  splits_enabled = COALESCE(%s, splits_enabled)
               WHERE id = %s RETURNING id""",
            (Jsonb(asdict(config)) if config is not None else None, splits_enabled, event_id),
        ).fetchone()
    return row is not None
