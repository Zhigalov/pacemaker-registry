from dataclasses import replace
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.db import Registry, RegistryEvent, RegistryPacemaker, RegistryResult, _sort_pacemakers, load_registry
from pacemaker_registry.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


def result(event_id, chip):
    return RegistryResult(1, event_id, "Забег", "10", "01:00", "06:00", chip, "06:00", 10, None, 10, "excellent", "", ())


def test_sort_uses_displayed_rating_then_performances_then_absolute_mean_then_name():
    a = RegistryPacemaker(1, "А Первый", "АП", 10, "excellent", (result(1, "01:00:01"),))
    b = replace(a, id=2, full_name="Б Второй", results=(result(1, "00:59:50"), result(2, "01:00:10")))
    c = replace(b, id=3, full_name="В Третий", results=(result(1, "00:59:58"), result(2, "01:00:04")))
    lower = replace(c, id=4, rating=9.94, total_result_count=10)
    assert b.mean_finish_deviation == 10  # Early and late finishes do not cancel.
    assert c.mean_finish_deviation == 3
    assert [p.id for p in _sort_pacemakers([a, b, lower, c])] == [3, 2, 1, 4]
    duplicate_event = replace(b, results=(result(1, "01:00:02"), result(1, "01:00:03")))
    assert duplicate_event.performance_count == 2
    same = replace(c, id=5, full_name="А Третий")
    assert [p.id for p in _sort_pacemakers([c, same])] == [5, 3]


def test_min_events_uses_history_and_keeps_event_options(monkeypatch):
    events = [(1, "Первое", "round", None, None), (2, "Второе", "round", None, None)]
    rows = [(i, "Тестов", str(i), i, 1, "Первое", Decimal(10), "01:00", "00:59:59", "06:00", [], count)
            for i, count in [(1, 1), (2, 3)]]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, parameters=()):
            self.rows = events if "SELECT e.id" in query else rows
            return self
        def fetchall(self): return self.rows
    monkeypatch.setattr("pacemaker_registry.db.connect_database", Connection)
    registry = load_registry(1, min_events=2)
    assert [p.id for p in registry.pacemakers] == [2]
    assert registry.pacemakers[0].performance_count == 3
    assert len(registry.events) == 2
    assert registry.result_count == 1
    assert registry.event_count == 1
    empty = load_registry(1, min_events=4)
    assert not empty.pacemakers
    assert empty.result_count == empty.event_count == 0
    assert len(empty.events) == 2


def test_two_distances_outrank_one_when_displayed_rating_is_equal():
    dyskin = RegistryPacemaker(1, "Дыскин Артём", "ДА", 10, "excellent", (result(1, "01:00:00"),))
    andreev = RegistryPacemaker(2, "Андреев Артем", "АА", 9.996847330473258, "excellent",
                               (result(2, "00:59:53"), result(2, "00:59:58")))
    assert format(andreev.rating, '.1f') == format(dyskin.rating, '.1f') == '10.0'
    assert andreev.performance_count == 2
    assert [p.id for p in _sort_pacemakers([dyskin, andreev])] == [2, 1]
    # Within a displayed tie, a tiny raw-score difference must not override accuracy.
    precise = replace(andreev, id=3, rating=9.96, results=(result(2, "01:00:01"), result(2, "01:00:01")))
    assert [p.id for p in _sort_pacemakers([andreev, precise])] == [3, 2]


@pytest.mark.anyio
async def test_min_events_parameter_validation_and_empty_state(monkeypatch):
    calls = []
    def load(event_id=None, include_splits=True, min_events=1):
        calls.append((event_id, include_splits, min_events))
        return Registry((), 0, 0, (RegistryEvent(1, "Первое"),), event_id, include_splits, min_events)
    monkeypatch.setattr("pacemaker_registry.main.load_registry", load)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/")
        r = await client.get("/?event_id=1&include_splits=false&min_events=3")
        assert r.status_code == 200
        assert 'value="3" data-registry-filter' in r.text
        assert 'include_splits=false&amp;min_events=3' in r.text
        assert "Нет пейсеров по выбранным условиям" in r.text
        for value in ['0', '-1', '1.5', 'abc', '10001']:
            assert (await client.get('/?min_events=' + value)).status_code == 422
    assert calls == [(None, True, 1), (1, False, 3)]
