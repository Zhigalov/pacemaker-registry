from decimal import Decimal

import pytest

from pacemaker_registry.db import (
    RATING_MODE_AUTOMATIC,
    RATING_MODE_STRICT,
    RATING_MODE_SYMMETRIC,
    RegistryPacemaker,
    _calculate_target_pace,
    _format_time_difference,
    _sort_pacemakers,
    calculate_rating_for_difference,
    calculate_pacemaker_rating,
    load_registry,
    normalize_rating_mode,
    resolve_rating_mode,
)


def test_calculate_target_pace_from_flag_time_and_distance() -> None:
    assert _calculate_target_pace("01:54", Decimal("21.1")) == "05:24 /км"


def test_calculate_target_pace_rounds_to_nearest_second() -> None:
    assert _calculate_target_pace("00:30", Decimal("5")) == "06:00 /км"


@pytest.mark.parametrize(
    ("chip_time", "expected"),
    [
        ("02:00:00", 10),
        ("01:59:15", 9),
        ("01:58:30", 8),
    ],
)
def test_rating_is_linear_in_the_target_zone(
    chip_time: str, expected: float
) -> None:
    assert calculate_pacemaker_rating("02:00", chip_time) == pytest.approx(expected)


def test_symmetric_rating_treats_ninety_six_seconds_as_just_outside_corridor() -> None:
    rating = calculate_pacemaker_rating("01:39", "01:40:36")

    assert rating == pytest.approx(7.74, abs=0.01)


def test_rating_accepts_sub_hour_chip_time_without_hours() -> None:
    rating = calculate_pacemaker_rating("00:59", "59:05")

    assert rating == pytest.approx(9.89, abs=0.01)


def test_automatic_mode_selects_model_from_target_minutes() -> None:
    assert resolve_rating_mode("02:00", RATING_MODE_AUTOMATIC) == RATING_MODE_STRICT
    assert resolve_rating_mode("01:35", RATING_MODE_AUTOMATIC) == RATING_MODE_STRICT
    assert resolve_rating_mode("01:34", RATING_MODE_AUTOMATIC) == RATING_MODE_SYMMETRIC
    assert resolve_rating_mode("01:59", RATING_MODE_AUTOMATIC) == RATING_MODE_SYMMETRIC


def test_symmetric_mode_scores_equal_deviations_equally() -> None:
    early = calculate_rating_for_difference(-45, RATING_MODE_SYMMETRIC)
    late = calculate_rating_for_difference(45, RATING_MODE_SYMMETRIC)

    assert early == late == 9


def test_strict_mode_penalizes_late_finish_more_than_early_finish() -> None:
    early = calculate_rating_for_difference(-40, RATING_MODE_STRICT)
    late = calculate_rating_for_difference(40, RATING_MODE_STRICT)

    assert late < early


def test_unknown_rating_mode_falls_back_to_automatic() -> None:
    assert normalize_rating_mode("unknown") == RATING_MODE_AUTOMATIC


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


def test_load_registry_filters_results_and_keeps_all_event_options(
    monkeypatch,
) -> None:
    calls: list[tuple[str, tuple[int, ...]]] = []
    event_rows = [(1, "Когалымский полумарафон"), (2, "Московский марафон")]
    result_rows = [
        (
            7,
            "Петров",
            "Пётр",
            11,
            2,
            "Московский марафон",
            Decimal("10"),
            "00:55",
            "00:54:53",
            "05:29 /км",
            [],
        )
    ]

    class FakeQueryResult:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query: str, parameters=()):
            calls.append((query, parameters))
            return FakeQueryResult(event_rows if len(calls) == 1 else result_rows)

    monkeypatch.setattr(
        "pacemaker_registry.db.connect_database", lambda: FakeConnection()
    )

    registry = load_registry(event_id=2, rating_mode=RATING_MODE_SYMMETRIC)

    assert [(event.id, event.name) for event in registry.events] == event_rows
    assert registry.selected_event_id == 2
    assert registry.rating_mode == RATING_MODE_SYMMETRIC
    assert calls[1][1] == (2,)
    assert [pacemaker.full_name for pacemaker in registry.pacemakers] == [
        "Петров Пётр"
    ]
    assert registry.event_count == 1
    assert registry.result_count == 1
