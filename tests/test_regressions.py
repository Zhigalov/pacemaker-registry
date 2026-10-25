import asyncio
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry import main
from pacemaker_registry.russiarunning import RaceResult, InvalidResultUrl
from pacemaker_registry.sources import load_race_result
from pacemaker_registry.times import normalize_target_time, elapsed_seconds


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def sample_result():
    return RaceResult('Тестов Первый', 'test-event', 'edf120ca-d0dd-408e-94d2-52bb06f4f101',
                      'Тестовый забег', '10', '00:49:55', '00:50', '05:00', (),
                      'https://results.runc.run/event/test/result/1/')


@pytest.mark.parametrize('value', ['00:00', '0:00', '01:60', '-1:00', '01:00:00', 'abc'])
def test_target_time_rejects_invalid_or_zero(value):
    with pytest.raises(ValueError):
        normalize_target_time(value)


def test_time_conventions_remain_distinct():
    assert normalize_target_time(' 1:05 ') == '01:05'
    assert elapsed_seconds('65:07') == elapsed_seconds('01:05:07') == 3907


@pytest.mark.anyio
async def test_zero_flag_never_fetches_or_saves(monkeypatch):
    fetch, save = AsyncMock(), Mock()
    monkeypatch.setattr(main, 'load_race_result', fetch)
    monkeypatch.setattr(main, 'save_race_result', save)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/results', data={'result_url': 'https://example.com', 'target_time': '00:00'})
    assert response.status_code == 422
    fetch.assert_not_called()
    save.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize('path,method', [('/add?result_url=https://example.com', 'get'),
                                        ('/add', 'post'), ('/results', 'post')])
async def test_database_does_not_block_event_loop(monkeypatch, sample_result, path, method):
    started, release = threading.Event(), threading.Event()

    def blocking(*args):
        started.set()
        if not release.wait(3):
            raise RuntimeError('Test worker was not released')
        return True if path == '/results' else None

    monkeypatch.setattr(main, 'load_race_result', AsyncMock(return_value=sample_result))
    monkeypatch.setattr(main, 'get_event_target_time_type', blocking)
    monkeypatch.setattr(main, 'save_race_result', blocking)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        options = {} if method == 'get' else {'data': {'result_url': 'https://example.com', 'target_time': '00:50'}}
        pending = asyncio.create_task(client.request(method, path, **options))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not pending.done()  # The DB worker is still waiting.
            assert (await asyncio.wait_for(client.get('/health'), 1)).status_code == 200
        finally:
            release.set()
            response = await pending
    assert response.status_code == 200


@pytest.mark.anyio
async def test_malformed_source_url_returns_validation_error():
    with pytest.raises(InvalidResultUrl):
        await load_race_result('https://[bad')


@pytest.mark.anyio
async def test_startup_does_not_connect_or_change_schema(monkeypatch):
    def forbidden():
        pytest.fail('App startup must not run SQL')
    monkeypatch.setattr('pacemaker_registry.db.connect_database', forbidden)
    async with main.app.router.lifespan_context(main.app):
        pass
