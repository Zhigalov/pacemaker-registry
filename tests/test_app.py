import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.main import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_home_page_is_rendered() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "Реестр пейсмейкеров" in response.text
    assert 'href="/static/styles.css"' in response.text


@pytest.mark.anyio
async def test_liveness_endpoint() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_readiness_reports_database_failure(monkeypatch) -> None:
    def unavailable_database() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "pacemaker_registry.main.check_database", unavailable_database
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
