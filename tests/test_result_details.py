from dataclasses import asdict
from decimal import Decimal
import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry import db, main
from pacemaker_registry.rating import EventRatingConfig, PaceRatingConfig


@pytest.fixture
def anyio_backend():
    return "asyncio"


def details(checkpoints=None, include_splits=True):
    event = db.RegistryEvent(1, 'Марафон <тест>', 'sub_minute',
                             EventRatingConfig(left_score=6), PaceRatingConfig(tolerance=8))
    if checkpoints is None:
        checkpoints = tuple({'distance_km': str(i), 'segment_distance_km': '1',
                             'time': f'{i * 5}:00', 'pace_per_km': '05:00'} for i in range(1, 43))
    result = db._build_registry_result(12, event, Decimal('42.2'), '03:30', '03:29:55', '04:58 /км', checkpoints, include_splits)
    return db.ResultDetails('Тестов <Первый>', 'https://results.runc.run/event/marathon/result/123/', event, result)


@pytest.mark.anyio
async def test_detail_renders_every_split_readonly_graphs_and_context(monkeypatch):
    sample = details()
    monkeypatch.setattr(main, 'get_result_details', lambda result_id, include_splits: sample)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.get('/results/12?event_id=1&include_splits=true&min_events=3')
    assert response.status_code == 200
    assert response.text.count('<tr><td>') == 42
    assert '<td>42</td>' in response.text
    assert 'Тестов &lt;Первый&gt;' in response.text
    assert 'data-rating-widget' in response.text and 'data-pace-widget' in response.text
    assert '<form' not in response.text and '<input' not in response.text
    assert 'data-event-rating' not in response.text and 'data-pace-rating' not in response.text
    assert '"left_score": 6' in response.text and '"tolerance": 8' in response.text
    marker_sets = [json.loads(value) for value in re.findall(
        r'<script type="application/json" data-result-markers>(.*?)</script>', response.text)]
    assert marker_sets[0] == [{'label': 'Финиш', 'deviation': -5, 'score': sample.result.finish_rating}]
    assert len(marker_sets[1]) == 42
    assert marker_sets[1][-1]['label'] == '42 км'
    assert marker_sets[1][0]['deviation'] == pytest.approx(300 - 12600 / 42.2)
    assert 'href="/?event_id=1&amp;include_splits=true&amp;min_events=3"' in response.text
    assert 'href="/events/1"' in response.text
    assert 'href="https://results.runc.run/event/marathon/"' in response.text
    assert 'href="https://results.runc.run/event/marathon/result/123/"' in response.text


@pytest.mark.anyio
async def test_detail_finish_only_and_missing_splits(monkeypatch):
    def get(result_id, include_splits):
        return details(checkpoints=(), include_splits=include_splits)
    monkeypatch.setattr(main, 'get_result_details', get)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.get('/results/12?include_splits=false')
        assert 'Учёт темпа выключен' in response.text
        assert 'В источнике нет сохранённых контрольных точек' in response.text
        assert 'data-result-markers>[]</script>' in response.text
        response = await client.get('/results/12')
        assert 'Недостаточно данных по отсечкам' in response.text


@pytest.mark.anyio
async def test_detail_missing_db_unavailable_and_invalid_filters(monkeypatch):
    monkeypatch.setattr(main, 'get_result_details', lambda *args, **kwargs: None)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        assert (await client.get('/results/999')).status_code == 404
        assert (await client.get('/results/12?min_events=0')).status_code == 422
        assert (await client.get('/results/12?event_id=-1')).status_code == 422
        def unavailable(*args, **kwargs): raise RuntimeError('DB unavailable')
        monkeypatch.setattr(main, 'get_result_details', unavailable)
        assert (await client.get('/results/12')).status_code == 503


@pytest.mark.parametrize('source,expected', [
    ('https://results.russiarunning.com/participant/race2026/42km/abc-def',
     'https://results.russiarunning.com/event/race2026/results/42km'),
    ('https://results.runc.run/event/marathon/result/123/', 'https://results.runc.run/event/marathon/'),
    ('javascript:alert(1)', None), ('https://evil.example/event/a/result/1/', None),
    ('https://results.runc.run@evil.example/event/a/result/1/', None),
    ('https://results.runc.run/event/a/../../evil', None), ('https://[bad', None),
])
def test_source_links_safe_and_event_specific(source, expected):
    result_url, event_url = main._result_source_links(source)
    assert event_url == expected
    assert result_url == (source if expected else None)


def test_single_result_query_matches_registry_scoring(monkeypatch):
    sample = details()
    event, result = sample.event, sample.result
    row = (event.id, event.name, event.target_time_type, asdict(event.formula), asdict(event.pace_formula),
           'Тестов', '<Первый>', sample.source_url, result.id, Decimal('42.2'), result.target_time,
           result.chip_time, result.actual_pace, list(result.checkpoints))
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, query, parameters):
            calls.append((query, parameters))
            return self
        def fetchone(self): return row
    monkeypatch.setattr(db, 'connect_database', Connection)
    loaded = db.get_result_details(12)
    assert loaded == sample
    assert calls[0][1] == (12,)
    assert 'WHERE rr.id = %s' in calls[0][0]
    assert db.get_result_details(12, include_splits=False).result.rating == result.finish_rating


def test_chart_markers_match_weighted_score_and_custom_pace_formula():
    event = db.RegistryEvent(1, 'Тест', pace_rating_config=PaceRatingConfig(tolerance=2))
    checkpoints = (
        {'distance_km': '3,00', 'segment_distance_km': '3', 'pace_per_km': '05:40'},
        {'distance_km': '10,00', 'segment_distance_km': '7', 'pace_per_km': '06:20'},
    )
    result = db._build_registry_result(1, event, Decimal('10'), '01:00', '59:55', '05:59', checkpoints)
    assert result.finish_deviation == -5
    assert [point['deviation'] for point in result.pace_markers] == [-20, 20]
    assert [point['label'] for point in result.pace_markers] == ['3,00 км', '10,00 км']
    assert result.splits_rating == pytest.approx(
        (result.pace_markers[0]['score'] * 3 + result.pace_markers[1]['score'] * 7) / 10)


def test_chart_markers_skip_invalid_splits_but_keep_valid_partial_coverage():
    checkpoints = (
        {'distance_km': '1', 'segment_distance_km': '1', 'pace_per_km': '05:00'},
        {'distance_km': '2', 'segment_distance_km': '1', 'pace_per_km': 'нет'},
        {'distance_km': '3', 'segment_distance_km': '0', 'pace_per_km': '05:00'},
        {'distance_km': '4', 'segment_distance_km': 'NaN', 'pace_per_km': '05:00'},
        {},
    )
    result = details(checkpoints=checkpoints).result
    assert result.splits_rating is None
    assert len(result.pace_markers) == 1
    assert result.pace_markers[0]['label'] == '1 км'


def test_finish_marker_keeps_outlier_and_zero_deviations():
    event = db.RegistryEvent(1, 'Тест')
    for chip, deviation in [('01:00:00', 0), ('01:20:00', 1200), ('40:00', -1200)]:
        result = db._build_registry_result(1, event, Decimal('10'), '01:00', chip, '06:00', ())
        assert result.finish_deviation == deviation
        assert result.pace_markers == ()
