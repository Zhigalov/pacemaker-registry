from dataclasses import asdict, replace
from math import exp
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.db import RegistryEvent, get_event, save_event_rating, load_registry, _rating_tone
from pacemaker_registry.main import app
from pacemaker_registry.rating import (
    EventRatingConfig, calculate_rating_for_difference as score,
    default_event_rating, parse_event_rating,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("seconds,expected", [
    (-45, 8), (-27.5, 9), (-10, 10), (0, 10),
    (10, 10), (27.5, 9), (45, 8), (-75, 8 / exp(1)), (75, 8 / exp(1)),
])
def test_sub_minute_five_zones(seconds, expected):
    assert score(seconds, default_event_rating("sub_minute")) == pytest.approx(expected)


@pytest.mark.parametrize("seconds,expected", [
    (-90, 8 / exp(1)), (-60, 8), (-37.5, 9), (-15, 10), (0, 10),
    (30, 10 / exp(1)),
])
def test_round_time_defaults(seconds, expected):
    assert score(seconds, default_event_rating("round")) == pytest.approx(expected)


def test_config_handles_collapsed_zones_without_jumps_and_never_zero():
    config = EventRatingConfig(left_bad=-15)
    for boundary in (-15, 0):
        assert score(boundary, config) == 10
        assert score(boundary - 0.00001, config) == pytest.approx(10, abs=0.00001)
        assert score(boundary + 0.00001, config) == pytest.approx(10, abs=0.00001)
    assert 0 < score(-100000, config) < 0.001
    assert 0 < score(100000, config) < 0.001


def test_boundary_continuity_and_monotonicity():
    config = default_event_rating("sub_minute")
    for boundary in (-45, -10, 10, 45):
        assert score(boundary - 0.00001, config) == pytest.approx(score(boundary + 0.00001, config), abs=0.00001)
    values = [score(t, config) for t in range(0, 301)]
    assert all(a >= b > 0 for a, b in zip(values, values[1:]))


def test_sides_and_curvature_are_independent():
    config = default_event_rating("sub_minute")
    changed = replace(config, right_decay=15, right_curve=2)
    assert score(-75, changed) == score(-75, config)
    assert score(75, changed) < score(75, config)
    assert score(20, changed) == score(20, config)


@pytest.mark.parametrize("changes", [
    {"left_bad": -5}, {"right_bad": -1}, {"left_good": 1},
    {"right_good": -1}, {"left_bad": -601}, {"right_bad": 601},
    {"left_decay": 0}, {"right_decay": 301}, {"left_curve": 0.4},
    {"right_curve": 3.1}, {"right_curve": "nan"}, {"left_decay": "inf"},
    {"left_good": True}, {"left_curve": "oops"},
    {"left_score": 0}, {"right_score": 10.1}, {"left_score": "nan"},
])
def test_invalid_settings_rejected(changes):
    with pytest.raises(ValueError):
        parse_event_rating(asdict(EventRatingConfig()) | changes)


def test_complete_payload_required():
    with pytest.raises(ValueError):
        parse_event_rating({})
    with pytest.raises(ValueError):
        parse_event_rating(asdict(EventRatingConfig()) | {"unknown": 2})


@pytest.mark.anyio
async def test_event_save_reload_and_isolation(monkeypatch):
    events = {1: RegistryEvent(1, "Когалым", "sub_minute"),
              2: RegistryEvent(2, "Москва", "round")}
    monkeypatch.setattr("pacemaker_registry.main.get_event", events.get)
    def save(event_id, config):
        events[event_id] = replace(events[event_id], rating_config=config)
        return True
    monkeypatch.setattr("pacemaker_registry.main.save_event_rating", save)
    values = asdict(default_event_rating("sub_minute")) | {"left_bad": -90, "left_score": 6, "right_score": 9}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/events/1")
        assert page.status_code == 200
        assert "саб-минута" in page.text
        assert 'value="-45"' in page.text
        saved = await client.post("/events/1/rating", data=values, follow_redirects=True)
        assert saved.status_code == 200
        assert "Формула сохранена" in saved.text
        assert 'value="-90.0"' in saved.text
        assert events[1].formula.left_score == 6
        assert events[1].formula.right_score == 9
        assert events[2].formula == default_event_rating("round")
        missing = await client.get("/events/999")
        assert missing.status_code == 404
        invalid = await client.post("/events/1/rating", data=values | {"left_good": -100})
        assert invalid.status_code == 422
        assert events[1].formula.left_good == -10
        assert 'value="-100"' in invalid.text


@pytest.mark.anyio
async def test_save_failure_keeps_edits(monkeypatch):
    monkeypatch.setattr("pacemaker_registry.main.get_event", lambda _: RegistryEvent(1, "Забег", "round"))
    def unavailable(*_):
        raise RuntimeError("DB unavailable")
    monkeypatch.setattr("pacemaker_registry.main.save_event_rating", unavailable)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/events/1/rating", data=asdict(EventRatingConfig(left_bad=-80)))
    assert response.status_code == 503
    assert 'value="-80"' in response.text
    assert "Не удалось сохранить" in response.text


def test_database_roundtrip_uses_event_id_and_json(monkeypatch):
    calls = []
    stored = None
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, parameters):
            nonlocal stored
            calls.append((query, parameters))
            if query.startswith("UPDATE"):
                stored = parameters[0].obj
                self.result = (7,)
            else:
                self.result = (7, "Марафон", "round", stored)
            return self
        def fetchone(self): return self.result
    monkeypatch.setattr("pacemaker_registry.db.connect_database", Connection)
    config = EventRatingConfig(right_bad=25, right_curve=1.5)
    assert save_event_rating(7, config)
    assert get_event(7).formula == config
    assert calls[0][1][1] == 7


def test_legacy_configs_keep_eight_points_only_when_loading_database():
    legacy = asdict(default_event_rating("sub_minute"))
    del legacy["left_score"], legacy["right_score"]
    assert parse_event_rating(legacy, allow_legacy=True) == default_event_rating("sub_minute")
    with pytest.raises(ValueError):
        parse_event_rating(legacy)


def test_editable_scores_are_continuous_and_independent():
    config = replace(default_event_rating("sub_minute"), left_score=6, right_score=9)
    assert score(-45, config) == 6
    assert score(-27.5, config) == 8
    assert score(27.5, config) == 9.5
    assert score(45, config) == 9
    assert score(-75, config) == pytest.approx(6 / exp(1))
    assert score(75, config) == pytest.approx(9 / exp(1))
    for boundary in (-45, -10, 10, 45):
        assert score(boundary - 1e-6, config) == pytest.approx(score(boundary + 1e-6, config), abs=1e-5)
    collapsed = replace(config, right_bad=10)
    assert score(10, collapsed) == 10
    assert score(10 + 1e-6, collapsed) == pytest.approx(10, abs=1e-5)


@pytest.mark.anyio
async def test_events_list_is_readonly_and_links_to_editors(monkeypatch):
    events = (RegistryEvent(1, "Первое", "round"), RegistryEvent(2, "Второе", "sub_minute", EventRatingConfig(left_score=6)))
    monkeypatch.setattr("pacemaker_registry.main.list_events", lambda: events)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/events")
        assert response.status_code == 200
        assert response.text.count('data-rating-widget') == 2
        assert 'data-event-rating' not in response.text
        assert '<form' not in response.text
        assert 'href="/events/1"' in response.text
        assert 'href="/events/2"' in response.text
        assert '"left_score": 6' in response.text
        monkeypatch.setattr("pacemaker_registry.main.list_events", lambda: ())
        response = await client.get("/events")
        assert "Пока нет соревнований" in response.text


def test_registry_applies_each_events_formula_before_sorting(monkeypatch):
    events = [(1, "Первый", "round", None),
              (2, "Второй", "round", asdict(EventRatingConfig(right_good=10, right_bad=30)))]
    rows = [(id, "Тестов", name, id, id, event, Decimal("10"), "00:50", "00:50:05", "05:00 /км", [])
            for id, name, event in [(1, "Первый", "Первый"), (2, "Второй", "Второй")]]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, parameters=()):
            self.rows = events if "SELECT e.id" in query else rows
            return self
        def fetchall(self): return self.rows
    monkeypatch.setattr("pacemaker_registry.db.connect_database", Connection)
    registry = load_registry(include_splits=False)
    assert [p.id for p in registry.pacemakers] == [2, 1]
    assert registry.pacemakers[0].rating == 10
    assert registry.pacemakers[1].rating == pytest.approx(10 * exp(-5/30))


@pytest.mark.parametrize("rating,tone", [(10, "excellent"), (9.9, "good"), (8, "good"), (7.9, "low")])
def test_rating_colors_follow_three_zones(rating, tone):
    assert _rating_tone(rating) == tone
