from decimal import Decimal

import pytest

from pacemaker_registry.db import (
    RegistryPacemaker,
    _calculate_target_pace,
    _format_time_difference,
    _sort_pacemakers,
    calculate_pacemaker_rating,
)


def test_calculate_target_pace_from_flag_time_and_distance() -> None:
    assert _calculate_target_pace("01:54", Decimal("21.1")) == "05:24 /км"


def test_calculate_target_pace_rounds_to_nearest_second() -> None:
    assert _calculate_target_pace("00:30", Decimal("5")) == "06:00 /км"


@pytest.mark.parametrize(
    ("chip_time", "expected"),
    [
        ("02:00:00", 10),
        ("01:59:45", 9),
        ("01:59:30", 8),
    ],
)
def test_rating_is_linear_in_the_target_zone(
    chip_time: str, expected: float
) -> None:
    assert calculate_pacemaker_rating("02:00", chip_time) == expected


def test_ninety_six_seconds_late_scores_about_three() -> None:
    rating = calculate_pacemaker_rating("01:39", "01:40:36")

    assert rating == pytest.approx(3.01, abs=0.01)


def test_late_finish_is_worse_than_equally_early_finish() -> None:
    late = calculate_pacemaker_rating("02:00", "02:00:40")
    early = calculate_pacemaker_rating("02:00", "01:59:20")

    assert late < early


def test_exponential_rating_never_reaches_zero_for_realistic_result() -> None:
    assert calculate_pacemaker_rating("02:00", "03:00:00") > 0
    assert calculate_pacemaker_rating("02:00", "00:30:00") > 0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "ровно"), (-7, "−7 с"), (10, "+10 с"), (-70, "−1:10")],
)
def test_format_time_difference(seconds: int, expected: str) -> None:
    assert _format_time_difference(seconds) == expected


def test_pacemakers_are_sorted_by_rating_then_name() -> None:
    pacemakers = [
        RegistryPacemaker(1, "Петров Пётр", "ПП", 3.0, "low", ()),
        RegistryPacemaker(2, "Сидоров Семён", "СС", 9.5, "excellent", ()),
        RegistryPacemaker(3, "Алексеев Алексей", "АА", 9.5, "excellent", ()),
    ]

    sorted_pacemakers = _sort_pacemakers(pacemakers)

    assert [item.full_name for item in sorted_pacemakers] == [
        "Алексеев Алексей",
        "Сидоров Семён",
        "Петров Пётр",
    ]
