from decimal import Decimal

import pytest

from pacemaker_registry.db import (
    RegistryPacemaker,
    _target_time_type_from_target_time,
    _calculate_target_pace,
    _format_time_difference,
    _sort_pacemakers,
    calculate_combined_rating,
    calculate_rating_for_difference,
    calculate_pacemaker_rating,
    calculate_split_rating,
    calculate_splits_rating,
    load_registry,
    save_race_result,
)
from pacemaker_registry.russiarunning import RaceResult
from pacemaker_registry.rating import EventRatingConfig, default_event_rating


def test_target_time_type_is_derived_from_first_confirmed_flag_time() -> None:
    assert _target_time_type_from_target_time("01:40") == "round"
    assert _target_time_type_from_target_time("01:39") == "sub_minute"


def test_save_result_records_event_type_from_confirmed_target(
    monkeypatch,
) -> None:
    calls: list[tuple[str, tuple]] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query: str, parameters: tuple):
            calls.append((query, parameters))

        def fetchone(self):
            return (1,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        "pacemaker_registry.db.connect_database", lambda: FakeConnection()
    )
    result = RaceResult(
        athlete_name="Пейсер Первый",
        source_event_id="event-id",
        source_participant_id="827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
        event_name="Забег",
        distance_km="21,1",
        chip_time="01:38:53",
        target_time="01:39",
        pace="04:41 /км",
        checkpoints=(),
        source_url=(
            "https://results.russiarunning.com/participant/"
            "event/race/827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
        ),
    )

    assert save_race_result(result, "01:39") is True

    event_query, event_parameters = calls[1]
    assert "target_time_type = COALESCE" in event_query
    assert event_parameters == ("event-id", "Забег", "sub_minute")


def test_calculate_target_pace_from_flag_time_and_distance() -> None:
    assert _calculate_target_pace("01:54", Decimal("21.1")) == "05:24 /км"


def test_calculate_target_pace_rounds_to_nearest_second() -> None:
    assert _calculate_target_pace("00:30", Decimal("5")) == "06:00 /км"


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
    event_rows = [(1, "Когалымский полумарафон", "sub_minute", None), (2, "Московский марафон", "round", None)]
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
        include_splits=False,
    )

    assert [(event.id, event.name) for event in registry.events] == [(row[0], row[1]) for row in event_rows]
    assert registry.selected_event_id == 2
    assert registry.pacemakers[0].results[0].finish_rating == 10
    assert registry.include_splits is False
    assert calls[1][1] == (2,)
    assert [pacemaker.full_name for pacemaker in registry.pacemakers] == [
        "Петров Пётр"
    ]
    assert registry.event_count == 1
    assert registry.result_count == 1
