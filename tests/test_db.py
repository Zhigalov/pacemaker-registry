from decimal import Decimal

import pytest

from pacemaker_registry.db import (
    _calculate_target_pace,
    _format_time_difference,
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


def test_ten_seconds_late_matches_forty_seconds_early() -> None:
    late = calculate_pacemaker_rating("02:00", "02:00:10")
    early = calculate_pacemaker_rating("02:00", "01:59:20")

    assert late == pytest.approx(early, abs=0.01)
    assert late == pytest.approx(7.17, abs=0.01)


def test_exponential_rating_never_reaches_zero_for_realistic_result() -> None:
    assert calculate_pacemaker_rating("02:00", "03:00:00") > 0
    assert calculate_pacemaker_rating("02:00", "00:30:00") > 0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "ровно"), (-7, "−7 с"), (10, "+10 с"), (-70, "−1:10")],
)
def test_format_time_difference(seconds: int, expected: str) -> None:
    assert _format_time_difference(seconds) == expected
