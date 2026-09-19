from decimal import Decimal

from pacemaker_registry.db import _calculate_target_pace


def test_calculate_target_pace_from_flag_time_and_distance() -> None:
    assert _calculate_target_pace("01:54", Decimal("21.1")) == "05:24 /км"


def test_calculate_target_pace_rounds_to_nearest_second() -> None:
    assert _calculate_target_pace("00:30", Decimal("5")) == "06:00 /км"
