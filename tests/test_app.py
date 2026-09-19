import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.main import app
from pacemaker_registry.russiarunning import Checkpoint, RaceResult


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
    assert 'href="/add"' in response.text
    assert "Добавить результат забега" in response.text


@pytest.mark.anyio
async def test_add_form_renders_parsed_result(monkeypatch) -> None:
    async def fake_load_race_result(value: str) -> RaceResult:
        return RaceResult(
            athlete_name="Жигалов Сергей",
            event_name="Международный Когалымский полумарафон",
            distance_km="21,1",
            chip_time="01:53:53",
            target_time="01:54",
            pace="05:23 /км",
            checkpoints=(
                Checkpoint(
                    distance_km="5,00",
                    segment_distance_km="5,00",
                    time="27:29",
                    pace_per_km="05:25",
                ),
            ),
            source_url=value,
        )

    monkeypatch.setattr(
        "pacemaker_registry.main.load_race_result", fake_load_race_result
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/add",
            data={
                "result_url": "https://results.russiarunning.com/participant/event/race/827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
            },
        )

    assert response.status_code == 200
    assert "Жигалов Сергей" in response.text
    assert "01:53:53" in response.text
    assert 'value="01:54"' in response.text
    assert "27:29" in response.text
    assert "Отрезок" in response.text
    assert "Проверьте полученные данные" in response.text
    assert "Контрольные точки" in response.text


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
