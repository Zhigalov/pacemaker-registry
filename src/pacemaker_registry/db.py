import os
from dataclasses import asdict
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from pacemaker_registry.russiarunning import (
    TARGET_TIME_TYPE_ROUND,
    TARGET_TIME_TYPE_SUB_MINUTE,
    RaceResult,
    RussiaRunningError,
)


from pacemaker_registry.rating import parse_event_rating, parse_pace_rating, EventRatingConfig, PaceRatingConfig
from pacemaker_registry.models import Registry, RegistryEvent, RegistryPacemaker, ResultDetails
from pacemaker_registry.registry import _build_registry_result, _sort_pacemakers
from pacemaker_registry.times import normalize_target_time


def get_result_details(result_id: int, include_splits: bool = True) -> ResultDetails | None:
    with connect_database() as connection:
        row = connection.execute(
            """SELECT e.id, e.name, e.target_time_type, e.rating_config, e.pace_rating_config,
                      p.last_name, p.first_name, rr.source_url, rr.id, rr.distance_km,
                      rr.target_time, rr.chip_time, rr.pace, rr.checkpoints
               FROM race_results AS rr
               JOIN events AS e ON e.id = rr.event_id
               JOIN pacemakers AS p ON p.id = rr.pacemaker_id
               WHERE rr.id = %s""", (result_id,),
        ).fetchone()
    if row is None:
        return None
    event = _event_from_row(row[:5])
    return ResultDetails(
        athlete_name=f"{row[5]} {row[6]}", source_url=row[7], event=event,
        result=_build_registry_result(row[8], event, *row[9:], include_splits=include_splits),
    )


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
        connection.execute("""SELECT rr.id, e.rating_config, e.pace_rating_config, p.last_name
            FROM race_results AS rr
            JOIN events AS e ON e.id = rr.event_id
            JOIN pacemakers AS p ON p.id = rr.pacemaker_id
            LIMIT 0""")


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


def load_registry(
    event_id: int | None = None,
    include_splits: bool = True,
    min_events: int = 1,
) -> Registry:
    with connect_database() as connection:
        # Both queries must see the same events and formulas during concurrent saves.
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        event_rows = connection.execute(
            """
            SELECT e.id, e.name, e.target_time_type, e.rating_config, e.pace_rating_config
            FROM events AS e
            WHERE EXISTS (
                SELECT 1
                FROM race_results AS rr
                WHERE rr.event_id = e.id
            )
            ORDER BY lower(e.name), e.id
            """
        ).fetchall()
        events = tuple(_event_from_row(row) for row in event_rows)
        events_by_id = {event.id: event for event in events}
        selected_event_id = (
            event_id if event_id in {event.id for event in events} else None
        )

        # Cards need segment weights/paces, not full checkpoint details or chart markers.
        # Count all history once per pacemaker, before applying the event filter.
        query = """
            SELECT
                p.id,
                p.last_name,
                p.first_name,
                rr.id,
                e.id,
                rr.distance_km,
                rr.target_time,
                rr.chip_time,
                COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'segment_distance_km', point->'segment_distance_km',
                        'pace_per_km', point->'pace_per_km'
                    ))
                    FROM jsonb_array_elements(rr.checkpoints) AS point
                ), '[]'::jsonb) AS segments,
                history.total_result_count
            FROM pacemakers AS p
            JOIN (
                SELECT pacemaker_id, count(*) AS total_result_count
                FROM race_results
                GROUP BY pacemaker_id
                HAVING count(*) >= %s
            ) AS history ON history.pacemaker_id = p.id
            JOIN race_results AS rr ON rr.pacemaker_id = p.id
            JOIN events AS e ON e.id = rr.event_id
        """
        parameters: tuple[int, ...] = (min_events,)
        if selected_event_id is not None:
            query += " WHERE e.id = %s"
            parameters += (selected_event_id,)
        query += """
            ORDER BY
                lower(p.last_name),
                lower(p.first_name),
                rr.created_at DESC,
                lower(e.name)
        """
        rows = connection.execute(query, parameters).fetchall()

    grouped: dict[int, dict[str, Any]] = {}
    event_ids: set[int] = set()
    for row in rows:
        (
            pacemaker_id,
            last_name,
            first_name,
            result_id,
            event_id,
            distance,
            target_time,
            chip_time,
            checkpoints,
            total_result_count,
        ) = row
        event_ids.add(event_id)
        pacemaker = grouped.setdefault(
            pacemaker_id,
            {
                "full_name": f"{last_name} {first_name}",
                "initials": f"{last_name[0]}{first_name[0]}",
                "results": [],
                "total_result_count": total_result_count,
            },
        )
        pacemaker["results"].append(
            _build_registry_result(result_id, events_by_id[event_id], distance,
                                   target_time, chip_time, None, checkpoints, include_splits, details=False)
        )

    pacemakers = []
    for pacemaker_id, data in grouped.items():
        results = tuple(data["results"])
        rating = sum(result.rating for result in results) / len(results)
        pacemakers.append(
            RegistryPacemaker(
                id=pacemaker_id,
                full_name=data["full_name"],
                initials=data["initials"],
                rating=rating,
                results=results,
                total_result_count=data["total_result_count"],
            )
        )
    return Registry(
        pacemakers=_sort_pacemakers(pacemakers),
        event_count=len(event_ids),
        result_count=sum(len(pacemaker.results) for pacemaker in pacemakers),
        events=events,
        selected_event_id=selected_event_id,
        include_splits=include_splits,
        min_events=min_events,
    )


def save_race_result(result: RaceResult, target_time: str) -> bool:
    target_time = normalize_target_time(target_time)
    last_name, first_name = _split_athlete_name(result.athlete_name)
    checkpoints = [asdict(checkpoint) for checkpoint in result.checkpoints]
    distance = Decimal(result.distance_km.replace(",", "."))
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
            return cursor.fetchone() is not None


def _target_time_type_from_target_time(target_time: str) -> str:
    _, minutes = map(int, target_time.split(":"))
    if minutes % 5 == 4:
        return TARGET_TIME_TYPE_SUB_MINUTE
    return TARGET_TIME_TYPE_ROUND


def _split_athlete_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split(maxsplit=1)
    if len(parts) != 2:
        raise RussiaRunningError("Не удалось разделить фамилию и имя участника.")
    return parts[0], parts[1]


def _event_from_row(row: tuple) -> RegistryEvent:
    return RegistryEvent(
        id=row[0], name=row[1], target_time_type=row[2],
        rating_config=parse_event_rating(row[3], allow_legacy=True) if row[3] is not None else None,
        pace_rating_config=parse_pace_rating(row[4]) if row[4] is not None else None,
    )


def get_event(event_id: int) -> RegistryEvent | None:
    with connect_database() as connection:
        row = connection.execute(
            "SELECT id, name, target_time_type, rating_config, pace_rating_config FROM events WHERE id = %s",
            (event_id,),
        ).fetchone()
    return _event_from_row(row) if row else None


def list_events() -> tuple[RegistryEvent, ...]:
    with connect_database() as connection:
        rows = connection.execute(
            "SELECT id, name, target_time_type, rating_config, pace_rating_config FROM events ORDER BY lower(name), id"
        ).fetchall()
    return tuple(_event_from_row(row) for row in rows)


def save_event_rating(event_id: int, config: EventRatingConfig) -> bool:
    with connect_database() as connection:
        row = connection.execute(
            "UPDATE events SET rating_config = %s WHERE id = %s RETURNING id",
            (Jsonb(asdict(config)), event_id),
        ).fetchone()
    return row is not None


def save_event_pace_rating(event_id: int, config: PaceRatingConfig) -> bool:
    with connect_database() as connection:
        row = connection.execute(
            "UPDATE events SET pace_rating_config = %s WHERE id = %s RETURNING id",
            (Jsonb(asdict(config)), event_id),
        ).fetchone()
    return row is not None
