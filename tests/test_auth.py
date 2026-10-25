from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry import main


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@pytest.mark.parametrize('path', ['/events/1/rating', '/events/1/pace-rating', '/results/1/target-time'])
async def test_anonymous_or_wrong_password_cannot_edit(monkeypatch, path):
    load = Mock(side_effect=AssertionError('Unauthorized request reached data access'))
    monkeypatch.setattr(main, '_load_event_or_404', load)
    monkeypatch.setattr(main, '_load_result_or_404', load)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='https://test') as client:
        for auth in [None, ('test-admin', 'wrong'), ('wrong', 'test-only-password')]:
            response = await client.post(path, data={'target_time': '01:00', 'previous_target_time': '01:01'}, auth=auth)
            assert response.status_code == 401
            assert response.headers['www-authenticate'].startswith('Basic')
    load.assert_not_called()


@pytest.mark.anyio
async def test_missing_config_fails_closed_but_health_stays_public(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD')
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='https://test') as client:
        assert (await client.post('/events/1/rating')).status_code == 503
        assert (await client.get('/health')).status_code == 200
        assert (await client.get('/add')).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize('headers', [{'origin': 'https://evil.example'},
                                    {'origin': 'null'}, {'sec-fetch-site': 'cross-site'}])
async def test_cross_site_mutations_rejected(headers):
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='https://test',
                           auth=('test-admin', 'test-only-password')) as client:
        assert (await client.post('/events/1/rating', headers=headers)).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize('internal_scheme', ['http', 'https'])
async def test_authenticated_same_origin_request_reaches_handler(monkeypatch, internal_scheme):
    load = Mock(side_effect=main.HTTPException(404, 'Synthetic event'))
    monkeypatch.setattr(main, '_load_event_or_404', load)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url=f'{internal_scheme}://test',
                           auth=('test-admin', 'test-only-password')) as client:
        assert (await client.post('/events/1/rating', headers={'origin': 'https://test'})).status_code == 404
    load.assert_called_once_with(1)
