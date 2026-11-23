"""Source-independent result models, import errors and flag-time guessing."""
from dataclasses import dataclass
from pacemaker_registry.times import elapsed_seconds


TARGET_TIME_TYPE_ROUND = "round"
TARGET_TIME_TYPE_SUB_MINUTE = "sub_minute"


class InvalidResultUrl(ValueError):
    """The submitted URL is not a supported result URL."""


class ResultImportError(RuntimeError):
    """The source could not return a usable result."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    distance_km: str
    segment_distance_km: str
    time: str
    pace_per_km: str


@dataclass(frozen=True, slots=True)
class RaceResult:
    athlete_name: str
    source_event_id: str
    source_participant_id: str
    event_name: str
    distance_km: str
    chip_time: str
    target_time: str
    pace: str
    checkpoints: tuple[Checkpoint, ...]
    source_url: str


def guess_target_time(chip_time: str, target_time_type: str | None = None) -> str:
    """Guess the flag time from the nearest common pacemaker target."""
    try:
        chip_seconds = elapsed_seconds(chip_time)
    except ValueError as error:
        raise ResultImportError("Не удалось определить время на флаге.") from error
    center_minute = chip_seconds // 60
    candidate_minutes = range(max(0, center_minute - 5), center_minute + 7)
    allowed_remainders = {
        TARGET_TIME_TYPE_ROUND: (0,),
        TARGET_TIME_TYPE_SUB_MINUTE: (4,),
    }.get(target_time_type, (0, 4))
    candidates = [
        minute * 60
        for minute in candidate_minutes
        if minute % 5 in allowed_remainders
    ]
    target_seconds = min(
        candidates,
        key=lambda candidate: (
            abs(candidate - chip_seconds),
            candidate < chip_seconds,
        ),
    )
    target_minutes = target_seconds // 60
    return f"{target_minutes // 60:02d}:{target_minutes % 60:02d}"
