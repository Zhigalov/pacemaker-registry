from dataclasses import asdict, replace
from decimal import Decimal
from math import exp

import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry import db, main
from pacemaker_registry.rating import PaceRatingConfig, EventRatingConfig, calculate_split_rating, parse_pace_rating


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_defaults_preserve_previous_scores_and_both_time_types():
    for t in (i / 10 for i in range(-3000, 3001)):
        d = abs(t)
        expected = (10 if d <= 5 else 10 - (d - 5) / 5 if d <= 10
                    else 9 - (d - 10) / 10 if d <= 30 else 7 * exp(-(d - 30) / 15))
        assert calculate_split_rating(t) == pytest.approx(expected)
    for time_type in (None, "round", "sub_minute"):
        assert db.RegistryEvent(1, "Тест", time_type).pace_formula == PaceRatingConfig()


def test_custom_thresholds_scores_continuity_symmetry_and_monotonicity():
    config = PaceRatingConfig(tolerance=8, checkpoint=16, bad=40, checkpoint_score=8, bad_score=4, decay=20, curve=1.5)
    for t, expected in [(0, 10), (8, 10), (12, 9), (16, 8), (28, 6), (40, 4), (60, 4 / exp(1))]:
        assert calculate_split_rating(t, config) == pytest.approx(expected)
        assert calculate_split_rating(-t, config) == pytest.approx(expected)
    for t in (8, 16, 40):
        assert calculate_split_rating(t - 1e-6, config) == pytest.approx(calculate_split_rating(t + 1e-6, config), abs=1e-5)
    values = [calculate_split_rating(t, config) for t in range(10000)]
    assert all(a >= b > 0 for a, b in zip(values, values[1:]))


@pytest.mark.parametrize("change", [
    {"tolerance": -1}, {"tolerance": 10}, {"checkpoint": 5}, {"checkpoint": 30},
    {"bad": 601}, {"bad_score": 0}, {"bad_score": 9.1}, {"checkpoint_score": 10.1},
    {"decay": 0}, {"decay": 301}, {"curve": .4}, {"curve": 3.1},
    {"bad": "nan"}, {"decay": "inf"}, {"curve": "oops"}, {"tolerance": True},
    {"curve": None}, {"unexpected": 1},
])
def test_invalid_settings_rejected(change):
    with pytest.raises(ValueError):
        parse_pace_rating(asdict(PaceRatingConfig()) | change)


def test_payload_required_and_string_form_supported():
    for value in ({}, None, [], {"tolerance": 10}):
        with pytest.raises(ValueError):
            parse_pace_rating(value)
    assert parse_pace_rating({k: str(v) for k, v in asdict(PaceRatingConfig()).items()}) == PaceRatingConfig()


def test_weighting_coverage_and_finish_only_unchanged():
    config = PaceRatingConfig(tolerance=0, checkpoint=10, bad=30)
    checkpoints = [{"segment_distance_km": "2", "pace_per_km": "05:00 /км"},
                   {"segment_distance_km": "8", "pace_per_km": "05:10 /км"}]
    assert db.calculate_splits_rating("00:50", Decimal("10"), checkpoints, config) == pytest.approx(9.2)
    assert db.calculate_splits_rating("00:50", Decimal("10"), checkpoints[:1], config) is None
    assert db.calculate_combined_rating(8, 4, False) == 8
    assert db.calculate_combined_rating(8, 4) == pytest.approx(6.4)


@pytest.mark.anyio
async def test_save_reload_event_and_finish_isolation(monkeypatch):
    finish = EventRatingConfig(left_bad=-90, left_score=6)
    events = {1: db.RegistryEvent(1, "Первое", "round", finish), 2: db.RegistryEvent(2, "Второе", "sub_minute")}
    monkeypatch.setattr(main, "get_event", events.get)
    def save(event_id, config):
        events[event_id] = replace(events[event_id], pace_rating_config=config)
        return True
    monkeypatch.setattr(main, "save_event_pace_rating", save)
    values = asdict(PaceRatingConfig(tolerance=8, checkpoint=15, bad=45))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as client:
        page = await client.get("/events/1")
        assert 'action="/events/1/pace-rating"' in page.text
        assert 'data-pace-rating' in page.text
        saved = await client.post("/events/1/pace-rating", data=values, follow_redirects=True)
        assert saved.status_code == 200
        assert "Формула сохранена" in saved.text
        assert 'value="45.0"' in saved.text
        assert events[1].pace_formula == PaceRatingConfig(**values)
        assert events[1].formula == finish
        assert events[2].pace_formula == PaceRatingConfig()
        invalid = await client.post("/events/1/pace-rating", data=values | {"tolerance": 90})
        assert invalid.status_code == 422
        assert 'value="90"' in invalid.text
        assert events[1].pace_formula.tolerance == 8
        assert (await client.post("/events/999/pace-rating", data=values)).status_code == 404


@pytest.mark.anyio
async def test_db_error_keeps_pace_edits_and_deleted_event_404(monkeypatch):
    monkeypatch.setattr(main, "get_event", lambda _: db.RegistryEvent(1, "Тест"))
    def unavailable(*_):
        raise RuntimeError("DB unavailable")
    monkeypatch.setattr(main, "save_event_pace_rating", unavailable)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as client:
        values = asdict(PaceRatingConfig(bad=55))
        page = await client.post("/events/1/pace-rating", data=values)
        assert page.status_code == 503
        assert 'value="55"' in page.text
        monkeypatch.setattr(main, "save_event_pace_rating", lambda *_: False)
        assert (await client.post("/events/1/pace-rating", data=values)).status_code == 404


def test_database_roundtrip_only_updates_pace_and_scopes_event_id(monkeypatch):
    calls = []
    stored = None
    finish = asdict(EventRatingConfig(left_bad=-90))
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, parameters):
            nonlocal stored
            calls.append((query, parameters))
            if query.startswith("UPDATE"):
                stored = parameters[0].obj
                self.row = (7,)
            else:
                self.row = (7, "Тест", "round", finish, stored)
            return self
        def fetchone(self): return self.row
    monkeypatch.setattr(db, "connect_database", Connection)
    config = PaceRatingConfig(tolerance=8, bad=45)
    assert db.save_event_pace_rating(7, config)
    event = db.get_event(7)
    assert event.pace_formula == config
    assert asdict(event.formula) == finish
    assert calls[0][0] == "UPDATE events SET pace_rating_config = %s WHERE id = %s RETURNING id"
    assert calls[0][1][1] == 7


def test_registry_uses_each_events_pace_config_before_sorting(monkeypatch):
    events = [(1, "Первое", "round", None, None),
              (2, "Второе", "round", None, asdict(PaceRatingConfig(tolerance=20, checkpoint=25, bad=40)))]
    rows = [(i, "Тестов", str(i), i, i, "Тест", Decimal("10"), "00:50", "00:50:00", "05:00 /км",
             [{"segment_distance_km": "10", "pace_per_km": "05:20 /км"}], 1) for i in (1, 2)]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, parameters=()):
            self.rows = events if "SELECT e.id" in query else rows
            return self
        def fetchall(self): return self.rows
    monkeypatch.setattr(db, "connect_database", Connection)
    registry = db.load_registry()
    assert [p.id for p in registry.pacemakers] == [2, 1]
    assert [p.rating for p in registry.pacemakers] == pytest.approx([10, 9.2])
    assert registry.pacemakers[0].results[0].splits_rating == 10
    assert registry.pacemakers[1].results[0].splits_rating == 8
    assert all(p.rating == 10 for p in db.load_registry(include_splits=False).pacemakers)
