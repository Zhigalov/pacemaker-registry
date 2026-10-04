from decimal import Decimal

import pytest

from pacemaker_registry.db import (
    RATING_MODE_AUTOMATIC,
    RATING_MODE_CUSTOM,
    RATING_MODE_STRICT,
    RATING_MODE_SYMMETRIC,
    CustomRatingConfig,
    RegistryPacemaker,
    _calculate_target_pace,
    _format_time_difference,
    _sort_pacemakers,
    calculate_combined_rating,
    calculate_rating_for_difference,
    calculate_pacemaker_rating,
    calculate_split_rating,
    calculate_splits_rating,
    load_registry,
    normalize_rating_mode,
    parse_custom_rating_config,
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
        ("01:59:45", 9),
        ("01:59:30", 8),
        ("01:59:15", 7),
    ],
)
def test_rating_is_linear_in_the_target_zone(
    chip_time: str, expected: float
) -> None:
    assert calculate_pacemaker_rating("02:00", chip_time) == pytest.approx(expected)


def test_symmetric_rating_falls_quickly_outside_the_corridor() -> None:
    rating = calculate_pacemaker_rating("01:39", "01:40:36")

    assert rating == pytest.approx(1.28, abs=0.01)


def test_rating_accepts_sub_hour_chip_time_without_hours() -> None:
    rating = calculate_pacemaker_rating("00:59", "59:05")

    assert rating == pytest.approx(9.67, abs=0.01)


def test_automatic_mode_selects_model_from_target_minutes() -> None:
    assert resolve_rating_mode("02:00", RATING_MODE_AUTOMATIC) == RATING_MODE_STRICT
    assert resolve_rating_mode("01:35", RATING_MODE_AUTOMATIC) == RATING_MODE_STRICT
    assert resolve_rating_mode("01:34", RATING_MODE_AUTOMATIC) == RATING_MODE_SYMMETRIC
    assert resolve_rating_mode("01:59", RATING_MODE_AUTOMATIC) == RATING_MODE_SYMMETRIC


@pytest.mark.parametrize(("difference", "expected"), [(15, 9), (30, 8), (45, 7)])
def test_symmetric_mode_has_linear_fifteen_second_steps(
    difference: int, expected: float
) -> None:
    assert calculate_rating_for_difference(-difference, RATING_MODE_SYMMETRIC) == expected
    assert calculate_rating_for_difference(difference, RATING_MODE_SYMMETRIC) == expected


@pytest.mark.parametrize(("difference", "expected"), [(-15, 9), (-30, 8), (-45, 7)])
def test_strict_mode_has_linear_steps_before_target(
    difference: int, expected: float
) -> None:
    assert calculate_rating_for_difference(difference, RATING_MODE_STRICT) == expected


def test_exponential_penalty_is_steep_after_linear_corridor() -> None:
    assert calculate_rating_for_difference(75, RATING_MODE_SYMMETRIC) == pytest.approx(
        2.58, abs=0.01
    )
    assert calculate_rating_for_difference(45, RATING_MODE_STRICT) == pytest.approx(
        2.23, abs=0.01
    )


def test_strict_mode_penalizes_late_finish_more_than_early_finish() -> None:
    early = calculate_rating_for_difference(-40, RATING_MODE_STRICT)
    late = calculate_rating_for_difference(40, RATING_MODE_STRICT)

    assert late < early


def test_unknown_rating_mode_falls_back_to_automatic() -> None:
    assert normalize_rating_mode("unknown") == RATING_MODE_AUTOMATIC


def test_default_custom_rating_matches_symmetric_rating() -> None:
    for difference in (-75, -45, -30, -15, 0, 15, 30, 45, 75):
        assert calculate_rating_for_difference(
            difference,
            RATING_MODE_CUSTOM,
        ) == pytest.approx(
            calculate_rating_for_difference(difference, RATING_MODE_SYMMETRIC)
        )


def test_custom_rating_interpolates_user_support_points() -> None:
    config = CustomRatingConfig(points=(5, 6, 7, 9, 8, 7, 6))

    assert calculate_rating_for_difference(-45, RATING_MODE_CUSTOM, config) == 5
    assert calculate_rating_for_difference(-22, RATING_MODE_CUSTOM, config) == pytest.approx(
        6.5333, abs=0.001
    )
    assert calculate_rating_for_difference(0, RATING_MODE_CUSTOM, config) == 9


def test_custom_rating_parser_sanitizes_query_parameters() -> None:
    config = parse_custom_rating_config(
        "-2,2,3,11,5,6,7",
        "100",
        "-5",
        "2",
        "200",
    )

    assert config.points == (0.1, 2, 3, 10, 5, 6, 7)
    assert config.left_exponent_start == 65
    assert config.right_exponent_start == 0
    assert config.left_decay == 10
    assert config.right_decay == 90


def test_custom_rating_has_independent_exponential_sides() -> None:
    config = CustomRatingConfig(
        left_exponent_start=15,
        right_exponent_start=45,
        left_decay=10,
        right_decay=60,
    )

    assert calculate_rating_for_difference(-30, RATING_MODE_CUSTOM, config) < 3
    assert calculate_rating_for_difference(30, RATING_MODE_CUSTOM, config) == 8
    assert calculate_rating_for_difference(-75, RATING_MODE_CUSTOM, config) < 0.1
    assert calculate_rating_for_difference(75, RATING_MODE_CUSTOM, config) > 4


def test_custom_rating_parser_supports_legacy_shared_parameters() -> None:
    config = parse_custom_rating_config(
        None,
        None,
        None,
        None,
        None,
        "55",
        "20",
    )

    assert config.left_exponent_start == 55
    assert config.right_exponent_start == 55
    assert config.left_decay == 20
    assert config.right_decay == 20


def test_late_finish_is_worse_than_equally_early_finish() -> None:
    late = calculate_pacemaker_rating("02:00", "02:00:40")
    early = calculate_pacemaker_rating("02:00", "01:59:20")

    assert late < early


def test_exponential_rating_never_reaches_zero_for_realistic_result() -> None:
    assert calculate_pacemaker_rating("02:00", "03:00:00") > 0
    assert calculate_pacemaker_rating("02:00", "00:30:00") > 0


@pytest.mark.parametrize(
    ("deviation", "expected"),
    [(0, 10), (3, 9), (6, 8), (10, 7), (-6, 8)],
)
def test_split_rating_uses_symmetric_pace_corridor(
    deviation: float, expected: float
) -> None:
    assert calculate_split_rating(deviation) == pytest.approx(expected)


def test_split_rating_falls_exponentially_after_ten_seconds() -> None:
    assert calculate_split_rating(25) == pytest.approx(7 / 2.718281828, abs=0.01)
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

    assert rating == pytest.approx(8.4)


def test_splits_rating_requires_eighty_percent_distance_coverage() -> None:
    rating = calculate_splits_rating(
        "00:50",
        Decimal("10"),
        [{"segment_distance_km": "7,9", "pace_per_km": "05:00"}],
    )

    assert rating is None


def test_combined_rating_can_ignore_splits() -> None:
    assert calculate_combined_rating(9, 7, True) == pytest.approx(8.2)
    assert calculate_combined_rating(9, 7, False) == 9
    assert calculate_combined_rating(9, None, True) == 9


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

    registry = load_registry(
        event_id=2,
        rating_mode=RATING_MODE_SYMMETRIC,
        include_splits=False,
    )

    assert [(event.id, event.name) for event in registry.events] == event_rows
    assert registry.selected_event_id == 2
    assert registry.rating_mode == RATING_MODE_SYMMETRIC
    assert registry.include_splits is False
    assert calls[1][1] == (2,)
    assert [pacemaker.full_name for pacemaker in registry.pacemakers] == [
        "Петров Пётр"
    ]
    assert registry.event_count == 1
    assert registry.result_count == 1
