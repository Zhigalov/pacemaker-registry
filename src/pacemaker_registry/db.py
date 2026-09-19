import os
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import exp
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from pacemaker_registry.russiarunning import RaceResult, RussiaRunningError


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True, slots=True)
class RegistryResult:
    id: int
    event_id: int
    event_name: str
    distance_km: str
    target_time: str
    target_pace: str
    chip_time: str
    actual_pace: str
    rating: float
    rating_tone: str
    time_difference: str
    checkpoints: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class RegistryPacemaker:
    id: int
    full_name: str
    initials: str
    rating: float
    rating_tone: str
    results: tuple[RegistryResult, ...]


@dataclass(frozen=True, slots=True)
class Registry:
    pacemakers: tuple[RegistryPacemaker, ...]
    event_count: int
    result_count: int


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


def initialize_database() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect_database() as connection:
        connection.execute(schema)


def check_database() -> None:
    with connect_database() as connection:
        connection.execute("SELECT 1").fetchone()


def load_registry() -> Registry:
    with connect_database() as connection:
        rows = connection.execute(
            """
            SELECT
                p.id,
                p.last_name,
                p.first_name,
                rr.id,
                e.id,
                e.name,
                rr.distance_km,
                rr.target_time,
                rr.chip_time,
                rr.pace,
                rr.checkpoints
            FROM pacemakers AS p
            JOIN race_results AS rr ON rr.pacemaker_id = p.id
            JOIN events AS e ON e.id = rr.event_id
            ORDER BY
                lower(p.last_name),
                lower(p.first_name),
                rr.created_at DESC,
                lower(e.name)
            """
        ).fetchall()

    grouped: dict[int, dict[str, Any]] = {}
    event_ids: set[int] = set()
    for row in rows:
        (
            pacemaker_id,
            last_name,
            first_name,
            result_id,
            event_id,
            event_name,
            distance,
            target_time,
            chip_time,
            actual_pace,
            checkpoints,
        ) = row
        event_ids.add(event_id)
        pacemaker = grouped.setdefault(
            pacemaker_id,
            {
                "full_name": f"{last_name} {first_name}",
                "initials": f"{last_name[0]}{first_name[0]}",
                "results": [],
            },
        )
        rating = calculate_pacemaker_rating(target_time, chip_time)
        pacemaker["results"].append(
            RegistryResult(
                id=result_id,
                event_id=event_id,
                event_name=event_name,
                distance_km=_format_registry_distance(distance),
                target_time=target_time,
                target_pace=_calculate_target_pace(target_time, distance),
                chip_time=chip_time,
                actual_pace=actual_pace,
                rating=rating,
                rating_tone=_rating_tone(rating),
                time_difference=_format_time_difference(
                    _time_difference_seconds(target_time, chip_time)
                ),
                checkpoints=tuple(checkpoints),
            )
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
                rating_tone=_rating_tone(rating),
                results=results,
            )
        )
    return Registry(
        pacemakers=_sort_pacemakers(pacemakers),
        event_count=len(event_ids),
        result_count=len(rows),
    )


def save_race_result(result: RaceResult, target_time: str) -> bool:
    last_name, first_name = _split_athlete_name(result.athlete_name)
    checkpoints = [asdict(checkpoint) for checkpoint in result.checkpoints]
    distance = Decimal(result.distance_km.replace(",", "."))

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
                INSERT INTO events (source_event_id, name)
                VALUES (%s, %s)
                ON CONFLICT (source_event_id)
                DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (result.source_event_id, result.event_name),
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


def _split_athlete_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split(maxsplit=1)
    if len(parts) != 2:
        raise RussiaRunningError("Не удалось разделить фамилию и имя участника.")
    return parts[0], parts[1]


def _calculate_target_pace(target_time: str, distance_km: Decimal) -> str:
    hours, minutes = map(int, target_time.split(":"))
    total_seconds = hours * 3600 + minutes * 60
    seconds_per_km = int(
        (Decimal(total_seconds) / distance_km).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return f"{seconds_per_km // 60:02d}:{seconds_per_km % 60:02d} /км"


def calculate_pacemaker_rating(target_time: str, chip_time: str) -> float:
    """Rate how closely the chip time matches the flag time on a 0–10 scale."""
    difference = _time_difference_seconds(target_time, chip_time)
    if difference < -30:
        return 8 * exp((difference + 30) / 90)
    if difference <= 0:
        return 10 + difference / 15
    return 10 * exp(-difference / 80)


def _time_difference_seconds(target_time: str, chip_time: str) -> int:
    target_hours, target_minutes = map(int, target_time.split(":"))
    chip_hours, chip_minutes, chip_seconds = map(int, chip_time.split(":"))
    target_seconds = target_hours * 3600 + target_minutes * 60
    actual_seconds = chip_hours * 3600 + chip_minutes * 60 + chip_seconds
    return actual_seconds - target_seconds


def _format_time_difference(seconds: int) -> str:
    if seconds == 0:
        return "ровно"
    sign = "+" if seconds > 0 else "−"
    absolute = abs(seconds)
    if absolute < 60:
        return f"{sign}{absolute} с"
    minutes, remaining_seconds = divmod(absolute, 60)
    return f"{sign}{minutes}:{remaining_seconds:02d}"


def _rating_tone(rating: float) -> str:
    if rating >= 9:
        return "excellent"
    if rating >= 8:
        return "good"
    if rating >= 6:
        return "fair"
    return "low"


def _sort_pacemakers(
    pacemakers: list[RegistryPacemaker],
) -> tuple[RegistryPacemaker, ...]:
    return tuple(
        sorted(
            pacemakers,
            key=lambda pacemaker: (-pacemaker.rating, pacemaker.full_name.casefold()),
        )
    )


def _format_registry_distance(distance: Decimal) -> str:
    return format(distance.normalize(), "f").replace(".", ",")
