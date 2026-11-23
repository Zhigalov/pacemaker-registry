from pacemaker_registry.registry import (_format_time_difference, _sort_pacemakers, calculate_combined_rating, calculate_rating_for_difference, calculate_pacemaker_rating, calculate_split_rating, calculate_splits_rating)
from decimal import Decimal
from pacemaker_registry.times import format_pace, target_seconds

import pytest

from pacemaker_registry.db import (
    RegistryPacemaker,
    _target_time_type_from_target_time,
)
from pacemaker_registry.rating import EventRatingConfig, default_event_rating


def test_target_time_type_is_derived_from_first_confirmed_flag_time() -> None:
    assert _target_time_type_from_target_time("01:40") == "round"
    assert _target_time_type_from_target_time("01:39") == "sub_minute"


def test_calculate_target_pace_from_flag_time_and_distance() -> None:
    assert format_pace(target_seconds("01:54"), Decimal("21.1")) == "05:24"


def test_calculate_target_pace_rounds_to_nearest_second() -> None:
    assert format_pace(target_seconds("00:30"), Decimal("5")) == "06:00"


def test_rating_uses_event_formula_not_target_minutes() -> None:
    config = default_event_rating("sub_minute")
    assert calculate_pacemaker_rating("00:59", "59:05", config) == 10
    assert calculate_pacemaker_rating("01:00", "01:00:05", config) == 10


@pytest.mark.parametrize(
    ("deviation", "expected"),
    [
        (0, 10),
        (5, 10),
        (-5, 10),
        (7.5, 9.5),
        (10, 9),
        (-10, 9),
        (20, 8),
        (30, 7),
    ],
)
def test_split_rating_uses_symmetric_pace_corridor(
    deviation: float, expected: float
) -> None:
    assert calculate_split_rating(deviation) == pytest.approx(expected)


def test_split_rating_falls_exponentially_after_thirty_seconds() -> None:
    assert calculate_split_rating(45) == pytest.approx(7 / 2.718281828, abs=0.01)
    assert calculate_split_rating(1000) > 0


def test_splits_rating_is_weighted_by_segment_distance() -> None:
    rating = calculate_splits_rating(
        "00:50",
        Decimal("10"),
        [
            {"segment_distance_km": "2", "pace_per_km": "05:00"},
            {"segment_distance_km": "8", "pace_per_km": "05:06"},
        ],
    )

    assert rating == pytest.approx(9.84)


def test_splits_rating_requires_eighty_percent_distance_coverage() -> None:
    rating = calculate_splits_rating(
        "00:50",
        Decimal("10"),
        [{"segment_distance_km": "7,9", "pace_per_km": "05:00"}],
    )

    assert rating is None


def test_combined_rating_uses_only_available_splits() -> None:
    assert calculate_combined_rating(9, 7) == pytest.approx(8.2)
    assert calculate_combined_rating(9, None) == 9


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "ровно"), (-7, "−7 с"), (10, "+10 с"), (-70, "−1:10")],
)
def test_format_time_difference(seconds: int, expected: str) -> None:
    assert _format_time_difference(seconds) == expected


def test_pacemakers_are_sorted_by_rating_then_name() -> None:
    pacemakers = [
        RegistryPacemaker(1, "Петров Пётр", "ПП", 3.0, ()),
        RegistryPacemaker(2, "Сидоров Семён", "СС", 9.5, ()),
        RegistryPacemaker(3, "Алексеев Алексей", "АА", 9.5, ()),
    ]

    sorted_pacemakers = _sort_pacemakers(pacemakers)

    assert [item.full_name for item in sorted_pacemakers] == [
        "Алексеев Алексей",
        "Сидоров Семён",
        "Петров Пётр",
    ]
