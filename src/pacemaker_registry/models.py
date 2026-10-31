"""Immutable view models, independent of SQL and HTTP."""
from dataclasses import dataclass
from typing import Any
from pacemaker_registry.rating import EventRatingConfig, PaceRatingConfig, default_event_rating
from pacemaker_registry.times import time_difference_seconds as _time_difference_seconds


@dataclass(frozen=True, slots=True)
class RegistryCard:
    id: int
    event_id: int
    event_name: str
    distance_km: str
    target_time: str
    chip_time: str
    finish_rating: float
    splits_rating: float | None
    rating: float

    @property
    def target_time_display(self) -> str:
        hours, minutes = map(int, self.target_time.split(":"))
        return f"{hours:02d}:{minutes:02d}:00"

    @property
    def chip_time_display(self) -> str:
        parts = list(map(int, self.chip_time.split(":")))
        if len(parts) == 2:
            parts.insert(0, 0)
        hours, minutes, seconds = parts
        # Sources may return elapsed minutes greater than 59 (e.g. 65:07).
        hours += minutes // 60
        return f"{hours:02d}:{minutes % 60:02d}:{seconds:02d}"

    @property
    def finish_difference_display(self) -> str:
        difference = _time_difference_seconds(self.target_time, self.chip_time)
        sign = "+" if difference > 0 else "−" if difference < 0 else ""
        return f"{sign}{abs(difference)} с"


@dataclass(frozen=True, slots=True)
class RegistryResult(RegistryCard):
    """Full information loaded only for an individual performance page."""
    target_pace: str
    actual_pace: str
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
    results: tuple[RegistryCard, ...]
    total_result_count: int = 0

    @property
    def performance_count(self) -> int:
        return self.total_result_count or len(self.results)

    @property
    def mean_finish_deviation(self) -> float:
        return (sum(abs(_time_difference_seconds(result.target_time, result.chip_time))
                    for result in self.results) / len(self.results)) if self.results else float("inf")


@dataclass(frozen=True, slots=True)
class PacemakerProfile:
    id: int
    last_name: str
    first_name: str
    motto: str | None = None
    avatar_key: str | None = None
    contacts: tuple[dict[str, str], ...] = ()
    achievements: tuple[str, ...] = ()
    version: int = 0
    results: tuple[RegistryCard, ...] = ()

    @property
    def full_name(self) -> str:
        return f'{self.last_name} {self.first_name}'

    @property
    def initials(self) -> str:
        return self.last_name[0] + self.first_name[0]

    @property
    def rating(self) -> float | None:
        return sum(r.rating for r in self.results) / len(self.results) if self.results else None


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
