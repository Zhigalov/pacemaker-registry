import os
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
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


from pacemaker_registry.rating import (
    EventRatingConfig, calculate_rating_for_difference, default_event_rating, parse_event_rating,
    PaceRatingConfig, calculate_split_rating, parse_pace_rating,
)


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
FINISH_RATING_WEIGHT = 0.6
SPLITS_RATING_WEIGHT = 0.4
MIN_SPLITS_COVERAGE = Decimal("0.8")


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
    finish_deviation: int = 0
    pace_markers: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class RegistryPacemaker:
    id: int
    full_name: str
    initials: str
    rating: float
    rating_tone: str
    results: tuple[RegistryResult, ...]
    total_result_count: int = 0

    @property
    def performance_count(self) -> int:
        return self.total_result_count or len(self.results)

    @property
    def mean_finish_deviation(self) -> float:
        return (sum(abs(_time_difference_seconds(result.target_time, result.chip_time))
                    for result in self.results) / len(self.results)) if self.results else float("inf")


@dataclass(frozen=True, slots=True)
class RegistryEvent:
    id: int
    name: str
    target_time_type: str | None = None
    rating_config: EventRatingConfig | None = None
    pace_rating_config: PaceRatingConfig | None = None

    @property
    def formula(self) -> EventRatingConfig:
        return self.rating_config or default_event_rating(self.target_time_type)

    @property
    def pace_formula(self) -> PaceRatingConfig:
        return self.pace_rating_config or PaceRatingConfig()


@dataclass(frozen=True, slots=True)
class Registry:
    pacemakers: tuple[RegistryPacemaker, ...]
    event_count: int
    result_count: int
    events: tuple[RegistryEvent, ...] = ()
    selected_event_id: int | None = None
    include_splits: bool = True
    min_events: int = 1


@dataclass(frozen=True, slots=True)
class ResultDetails:
    athlete_name: str
    source_url: str
    event: RegistryEvent
    result: RegistryResult


def _build_registry_result(result_id, event, distance, target_time, chip_time,
                           actual_pace, checkpoints, include_splits=True) -> RegistryResult:
    """The registry and detail page must use the very same scoring pipeline."""
    finish_rating = calculate_pacemaker_rating(target_time, chip_time, event.formula)
    splits_rating = calculate_splits_rating(target_time, distance, checkpoints, event.pace_formula)
    rating = calculate_combined_rating(finish_rating, splits_rating, include_splits)
    return RegistryResult(
        id=result_id, event_id=event.id, event_name=event.name,
        distance_km=_format_registry_distance(distance), target_time=target_time,
        target_pace=_calculate_target_pace(target_time, distance), chip_time=chip_time,
        actual_pace=actual_pace, finish_rating=finish_rating, splits_rating=splits_rating,
        rating=rating, rating_tone=_rating_tone(rating),
        time_difference=_format_time_difference(_time_difference_seconds(target_time, chip_time)),
        checkpoints=tuple(checkpoints),
        finish_deviation=_time_difference_seconds(target_time, chip_time),
        pace_markers=tuple({"label": str(point.get("distance_km", "?")) + " км",
                            "deviation": deviation, "score": score}
                           for point, _, deviation, score in
                           _rated_segments(target_time, distance, checkpoints, event.pace_formula)),
    )


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
                rr.checkpoints,
                (SELECT count(*)
                 FROM race_results AS history
                 WHERE history.pacemaker_id = p.id) AS total_result_count
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
            total_result_count,
        ) = row
        if total_result_count < min_events:
            continue
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
                                   target_time, chip_time, actual_pace, checkpoints, include_splits)
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


def calculate_pacemaker_rating(
    target_time: str,
    chip_time: str,
    config: EventRatingConfig,
) -> float:
    return calculate_rating_for_difference(
        _time_difference_seconds(target_time, chip_time), config
    )


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


def calculate_splits_rating(
    target_time: str,
    distance_km: Decimal,
    checkpoints: list[dict[str, str]] | tuple[dict[str, str], ...],
    config: PaceRatingConfig = PaceRatingConfig(),
) -> float | None:
    """Return a distance-weighted rating for checkpoint segment paces."""
    if distance_km <= 0:
        return None

    weighted_rating = 0.0
    covered_distance = Decimal("0")
    for _, segment_distance, _, score in _rated_segments(target_time, distance_km, checkpoints, config):
        weighted_rating += score * float(segment_distance)
        covered_distance += segment_distance

    if covered_distance < distance_km * MIN_SPLITS_COVERAGE:
        return None
    return weighted_rating / float(covered_distance)


def _rated_segments(target_time, distance_km, checkpoints, config):
    """One source for the weighted rating and exact per-segment chart markers."""
    if not distance_km.is_finite() or distance_km <= 0:
        return
    target_pace_seconds = _target_time_seconds(target_time) / float(distance_km)
    for checkpoint in checkpoints:
        try:
            segment_distance = Decimal(
                str(checkpoint["segment_distance_km"]).replace(",", ".")
            )
            pace_seconds = _pace_seconds(str(checkpoint["pace_per_km"]))
        except (KeyError, ValueError, ArithmeticError):
            continue
        if not segment_distance.is_finite() or segment_distance <= 0 or pace_seconds <= 0:
            continue
        deviation = pace_seconds - target_pace_seconds
        if isfinite(deviation):
            yield checkpoint, segment_distance, deviation, calculate_split_rating(deviation, config)


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
    if rating >= 7:
        return "good"
    return "low"


def _sort_pacemakers(
    pacemakers: list[RegistryPacemaker],
) -> tuple[RegistryPacemaker, ...]:
    return tuple(
        sorted(
            pacemakers,
            key=lambda pacemaker: (
                -float(format(pacemaker.rating, ".1f")),
                -pacemaker.performance_count,
                pacemaker.mean_finish_deviation,
                pacemaker.full_name.casefold(),
                pacemaker.id,
            ),
        )
    )


def _format_registry_distance(distance: Decimal) -> str:
    return format(distance.normalize(), "f").replace(".", ",")
