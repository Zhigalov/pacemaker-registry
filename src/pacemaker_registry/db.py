import os
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import exp
from pathlib import Path
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


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
RATING_MODE_AUTOMATIC = "automatic"
RATING_MODE_CUSTOM = "custom"
RATING_MODE_STRICT = "strict"
RATING_MODE_SYMMETRIC = "symmetric"
RATING_MODES = {
    RATING_MODE_AUTOMATIC,
    RATING_MODE_CUSTOM,
    RATING_MODE_STRICT,
    RATING_MODE_SYMMETRIC,
}
CUSTOM_RATING_SECONDS = (-45, -30, -15, 0, 15, 30, 45)
DEFAULT_CUSTOM_RATING_POINTS = (7.0, 8.0, 9.0, 10.0, 9.0, 8.0, 7.0)
FINISH_RATING_WEIGHT = 0.6
SPLITS_RATING_WEIGHT = 0.4
MIN_SPLITS_COVERAGE = Decimal("0.8")


@dataclass(frozen=True, slots=True)
class CustomRatingConfig:
    points: tuple[float, ...] = DEFAULT_CUSTOM_RATING_POINTS
    left_exponent_start: int = 45
    right_exponent_start: int = 45
    left_decay: int = 30
    right_decay: int = 30


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
    finish_rating: float
    splits_rating: float | None
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
class RegistryEvent:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Registry:
    pacemakers: tuple[RegistryPacemaker, ...]
    event_count: int
    result_count: int
    events: tuple[RegistryEvent, ...] = ()
    selected_event_id: int | None = None
    rating_mode: str = RATING_MODE_AUTOMATIC
    custom_rating: CustomRatingConfig = CustomRatingConfig()
    include_splits: bool = True


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
    rating_mode: str = RATING_MODE_AUTOMATIC,
    custom_rating: CustomRatingConfig = CustomRatingConfig(),
    include_splits: bool = True,
) -> Registry:
    rating_mode = normalize_rating_mode(rating_mode)
    with connect_database() as connection:
        event_rows = connection.execute(
            """
            SELECT e.id, e.name
            FROM events AS e
            WHERE EXISTS (
                SELECT 1
                FROM race_results AS rr
                WHERE rr.event_id = e.id
            )
            ORDER BY lower(e.name), e.id
            """
        ).fetchall()
        events = tuple(RegistryEvent(id=row[0], name=row[1]) for row in event_rows)
        selected_event_id = (
            event_id if event_id in {event.id for event in events} else None
        )

        query = """
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
        """
        parameters: tuple[int, ...] = ()
        if selected_event_id is not None:
            query += " WHERE e.id = %s"
            parameters = (selected_event_id,)
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
        finish_rating = calculate_pacemaker_rating(
            target_time,
            chip_time,
            rating_mode,
            custom_rating,
        )
        splits_rating = calculate_splits_rating(
            target_time,
            distance,
            checkpoints,
        )
        rating = calculate_combined_rating(
            finish_rating,
            splits_rating,
            include_splits,
        )
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
                finish_rating=finish_rating,
                splits_rating=splits_rating,
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
        events=events,
        selected_event_id=selected_event_id,
        rating_mode=rating_mode,
        custom_rating=custom_rating,
        include_splits=include_splits,
    )


def save_race_result(result: RaceResult, target_time: str) -> bool:
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


def _calculate_target_pace(target_time: str, distance_km: Decimal) -> str:
    hours, minutes = map(int, target_time.split(":"))
    total_seconds = hours * 3600 + minutes * 60
    seconds_per_km = int(
        (Decimal(total_seconds) / distance_km).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return f"{seconds_per_km // 60:02d}:{seconds_per_km % 60:02d} /км"


def normalize_rating_mode(rating_mode: str) -> str:
    return rating_mode if rating_mode in RATING_MODES else RATING_MODE_AUTOMATIC


def parse_custom_rating_config(
    serialized_points: str | None,
    left_exponent_start: str | None,
    right_exponent_start: str | None,
    left_decay: str | None,
    right_decay: str | None,
    legacy_exponent_start: str | None = None,
    legacy_decay: str | None = None,
) -> CustomRatingConfig:
    points = DEFAULT_CUSTOM_RATING_POINTS
    if serialized_points:
        try:
            parsed = tuple(float(value) for value in serialized_points.split(","))
            if len(parsed) == len(CUSTOM_RATING_SECONDS):
                points = tuple(min(10.0, max(0.1, value)) for value in parsed)
        except ValueError:
            pass

    def parse_integer(value: str | None, fallback: str | None, default: int) -> int:
        try:
            return int(value or fallback or default)
        except ValueError:
            return default

    parsed_left_start = parse_integer(
        left_exponent_start, legacy_exponent_start, 45
    )
    parsed_right_start = parse_integer(
        right_exponent_start, legacy_exponent_start, 45
    )
    parsed_left_decay = parse_integer(left_decay, legacy_decay, 30)
    parsed_right_decay = parse_integer(right_decay, legacy_decay, 30)

    return CustomRatingConfig(
        points=points,
        left_exponent_start=min(65, max(0, parsed_left_start)),
        right_exponent_start=min(65, max(0, parsed_right_start)),
        left_decay=min(90, max(10, parsed_left_decay)),
        right_decay=min(90, max(10, parsed_right_decay)),
    )


def serialize_custom_rating_points(config: CustomRatingConfig) -> str:
    return ",".join(f"{point:g}" for point in config.points)


def resolve_rating_mode(target_time: str, rating_mode: str) -> str:
    rating_mode = normalize_rating_mode(rating_mode)
    if rating_mode != RATING_MODE_AUTOMATIC:
        return rating_mode
    _, target_minutes = map(int, target_time.split(":"))
    if target_minutes % 10 in {4, 9}:
        return RATING_MODE_SYMMETRIC
    return RATING_MODE_STRICT


def calculate_rating_for_difference(
    difference: int,
    rating_mode: str,
    custom_rating: CustomRatingConfig = CustomRatingConfig(),
) -> float:
    if rating_mode == RATING_MODE_CUSTOM:
        return _calculate_custom_rating(difference, custom_rating)

    if rating_mode == RATING_MODE_SYMMETRIC:
        absolute_difference = abs(difference)
        if absolute_difference <= 45:
            return 10 - absolute_difference / 15
        return 7 * exp(-(absolute_difference - 45) / 30)

    if difference < -45:
        return 7 * exp((difference + 45) / 30)
    if difference <= 0:
        return 10 + difference / 15
    return 10 * exp(-difference / 30)


def calculate_pacemaker_rating(
    target_time: str,
    chip_time: str,
    rating_mode: str = RATING_MODE_AUTOMATIC,
    custom_rating: CustomRatingConfig = CustomRatingConfig(),
) -> float:
    """Rate how closely the chip time matches the flag time on a 0–10 scale."""
    difference = _time_difference_seconds(target_time, chip_time)
    resolved_mode = resolve_rating_mode(target_time, rating_mode)
    return calculate_rating_for_difference(difference, resolved_mode, custom_rating)


def calculate_split_rating(deviation_seconds_per_km: float) -> float:
    """Rate an absolute pace deviation in seconds per kilometre."""
    deviation = abs(deviation_seconds_per_km)
    if deviation <= 5:
        return 10
    if deviation <= 10:
        return 10 - (deviation - 5) / 5
    if deviation <= 30:
        return 9 - (deviation - 10) / 10
    return 7 * exp(-(deviation - 30) / 15)


def calculate_splits_rating(
    target_time: str,
    distance_km: Decimal,
    checkpoints: list[dict[str, str]] | tuple[dict[str, str], ...],
) -> float | None:
    """Return a distance-weighted rating for checkpoint segment paces."""
    if distance_km <= 0:
        return None

    target_pace_seconds = _target_time_seconds(target_time) / float(distance_km)
    weighted_rating = 0.0
    covered_distance = Decimal("0")
    for checkpoint in checkpoints:
        try:
            segment_distance = Decimal(
                str(checkpoint["segment_distance_km"]).replace(",", ".")
            )
            pace_seconds = _pace_seconds(str(checkpoint["pace_per_km"]))
        except (KeyError, ValueError, ArithmeticError):
            continue
        if segment_distance <= 0:
            continue
        weighted_rating += calculate_split_rating(
            pace_seconds - target_pace_seconds
        ) * float(segment_distance)
        covered_distance += segment_distance

    if covered_distance < distance_km * MIN_SPLITS_COVERAGE:
        return None
    return weighted_rating / float(covered_distance)


def calculate_combined_rating(
    finish_rating: float,
    splits_rating: float | None,
    include_splits: bool = True,
) -> float:
    if not include_splits or splits_rating is None:
        return finish_rating
    return (
        finish_rating * FINISH_RATING_WEIGHT
        + splits_rating * SPLITS_RATING_WEIGHT
    )


def _calculate_custom_rating(
    difference: int,
    config: CustomRatingConfig,
) -> float:
    if difference < 0:
        boundary = -config.left_exponent_start
        if difference >= boundary:
            return _calculate_custom_linear_rating(difference, config.points)
        boundary_score = _calculate_custom_linear_rating(boundary, config.points)
        return boundary_score * exp(
            (difference + config.left_exponent_start) / config.left_decay
        )

    boundary = config.right_exponent_start
    if difference <= boundary:
        return _calculate_custom_linear_rating(difference, config.points)
    boundary_score = _calculate_custom_linear_rating(boundary, config.points)
    return boundary_score * exp(
        -(difference - config.right_exponent_start) / config.right_decay
    )


def _calculate_custom_linear_rating(
    difference: int,
    points: tuple[float, ...],
) -> float:
    if difference < -45:
        edge_slope = (points[1] - points[0]) / 15
        return _clamp_rating(points[0] + edge_slope * (difference + 45))
    if difference > 45:
        edge_slope = (points[-1] - points[-2]) / 15
        return _clamp_rating(points[-1] + edge_slope * (difference - 45))

    position = (difference + 45) / 15
    left_index = min(int(position), len(points) - 2)
    fraction = position - left_index
    return points[left_index] + (points[left_index + 1] - points[left_index]) * fraction


def _clamp_rating(rating: float) -> float:
    return min(10.0, max(0.1, rating))


def _time_difference_seconds(target_time: str, chip_time: str) -> int:
    target_hours, target_minutes = map(int, target_time.split(":"))
    chip_parts = [int(part) for part in chip_time.split(":")]
    if len(chip_parts) == 2:
        chip_hours = 0
        chip_minutes, chip_seconds = chip_parts
    elif len(chip_parts) == 3:
        chip_hours, chip_minutes, chip_seconds = chip_parts
    else:
        raise ValueError(f"Unsupported chip time format: {chip_time!r}")
    target_seconds = target_hours * 3600 + target_minutes * 60
    actual_seconds = chip_hours * 3600 + chip_minutes * 60 + chip_seconds
    return actual_seconds - target_seconds


def _target_time_seconds(target_time: str) -> int:
    hours, minutes = map(int, target_time.split(":"))
    return hours * 3600 + minutes * 60


def _pace_seconds(pace: str) -> int:
    normalized = pace.replace("/км", "").strip()
    minutes, seconds = map(int, normalized.split(":"))
    return minutes * 60 + seconds


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
