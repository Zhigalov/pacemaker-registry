"""Registry assembly and scoring; no database or network operations."""
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite
from pacemaker_registry.models import RegistryResult, RegistryPacemaker
from pacemaker_registry.rating import (
    EventRatingConfig, PaceRatingConfig, calculate_rating_for_difference, calculate_split_rating,
)
from pacemaker_registry.times import (
    time_difference_seconds as _time_difference_seconds, target_seconds as _target_time_seconds,
)

FINISH_RATING_WEIGHT = 0.6
SPLITS_RATING_WEIGHT = 0.4
MIN_SPLITS_COVERAGE = Decimal("0.8")


def _build_registry_result(result_id, event, distance, target_time, chip_time,
                           actual_pace, checkpoints, include_splits=True) -> RegistryResult:
    """The registry and detail page must use the very same scoring pipeline."""
    finish_rating = calculate_pacemaker_rating(target_time, chip_time, event.formula)
    segments = tuple(_rated_segments(target_time, distance, checkpoints, event.pace_formula))
    splits_rating = _weighted_splits_rating(distance, segments)
    rating = calculate_combined_rating(finish_rating, splits_rating, include_splits)
    return RegistryResult(
        id=result_id, event_id=event.id, event_name=event.name,
        distance_km=_format_registry_distance(distance), target_time=target_time,
        target_pace=_calculate_target_pace(target_time, distance), chip_time=chip_time,
        actual_pace=actual_pace, finish_rating=finish_rating, splits_rating=splits_rating,
        rating=rating,
        time_difference=_format_time_difference(_time_difference_seconds(target_time, chip_time)),
        checkpoints=tuple(checkpoints),
        finish_deviation=_time_difference_seconds(target_time, chip_time),
        pace_markers=tuple({"label": str(point.get("distance_km", "?")) + " км",
                            "deviation": deviation, "score": score}
                           for point, _, deviation, score in
                           segments),
    )


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


def calculate_splits_rating(
    target_time: str,
    distance_km: Decimal,
    checkpoints: list[dict[str, str]] | tuple[dict[str, str], ...],
    config: PaceRatingConfig = PaceRatingConfig(),
) -> float | None:
    """Return a distance-weighted rating for checkpoint segment paces."""
    return _weighted_splits_rating(distance_km, _rated_segments(target_time, distance_km, checkpoints, config))


def _weighted_splits_rating(distance_km, segments) -> float | None:
    if not distance_km.is_finite() or distance_km <= 0:
        return None

    weighted_rating = 0.0
    covered_distance = Decimal("0")
    for _, segment_distance, _, score in segments:
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
