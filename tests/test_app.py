import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.db import Registry, RegistryPacemaker, RegistryResult
from pacemaker_registry.main import app
from pacemaker_registry.russiarunning import Checkpoint, RaceResult


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_home_page_is_rendered(monkeypatch) -> None:
    registry = Registry(
        pacemakers=(
            RegistryPacemaker(
                id=1,
                full_name="Жигалов Сергей",
                initials="ЖС",
                rating=9.533,
                rating_tone="excellent",
                results=(
                    RegistryResult(
                        id=1,
                        event_id=1,
                        event_name="Международный Когалымский полумарафон",
                        distance_km="21,1",
                        target_time="01:54",
                        target_pace="05:24 /км",
                        chip_time="01:53:53",
                        actual_pace="05:23 /км",
                        rating=9.533,
                        rating_tone="excellent",
                        time_difference="−7 с",
                        checkpoints=(
                            {
                                "distance_km": "5,00",
                                "segment_distance_km": "5,00",
                                "time": "27:29",
                                "pace_per_km": "05:25",
                            },
                        ),
                    ),
                ),
            ),
        ),
        event_count=1,
        result_count=1,
    )
    monkeypatch.setattr("pacemaker_registry.main.load_registry", lambda: registry)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "Реестр пейсмейкеров" in response.text
    assert 'href="/static/styles.css"' in response.text
    assert 'href="/add"' in response.text
    assert "Жигалов Сергей" in response.text
    assert "Международный Когалымский полумарафон" in response.text
    assert "05:24 /км" in response.text
    assert "05:23 /км" in response.text
    assert 'class="result-event"' not in response.text
    assert "Рейтинг 9,5 из 10" in response.text
    assert ">Цель<" in response.text
    assert ">Факт<" in response.text
    assert "−7 с" in response.text
    assert "Как считается рейтинг" in response.text
    assert "1:36" in response.text
    assert "3,0 балла" in response.text
    assert "Детали результата" in response.text
    assert "Здесь появится список выступлений" not in response.text


@pytest.mark.anyio
async def test_add_form_renders_parsed_result(monkeypatch) -> None:
    async def fake_load_race_result(value: str) -> RaceResult:
        return RaceResult(
            athlete_name="Жигалов Сергей",
            source_event_id="490b438c-8b18-4162-8382-4f1b480960cd",
            source_participant_id="827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
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
    assert "При необходимости скорректируйте время на флаге." in response.text
    assert "Убедитесь, что имя, время" not in response.text
    assert "Контрольные точки" in response.text
    assert "Сохранить в реестр" in response.text
    assert "Ожидаемый темп" in response.text
    assert 'data-distance="21,1"' in response.text
    assert 'src="/static/add.js"' in response.text
    assert "пока ничего не сохраняется" not in response.text
    assert response.text.index("Сохранить в реестр") < response.text.index(
        "Контрольные точки"
    )


@pytest.mark.anyio
async def test_result_can_be_saved_after_review(monkeypatch) -> None:
    result = RaceResult(
        athlete_name="Жигалов Сергей",
        source_event_id="490b438c-8b18-4162-8382-4f1b480960cd",
        source_participant_id="827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
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
        source_url=(
            "https://results.russiarunning.com/participant/event/race/"
            "827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
        ),
    )

    async def fake_load_race_result(_: str) -> RaceResult:
        return result

    saved: list[str] = []

    def fake_save_race_result(_: RaceResult, target_time: str) -> bool:
        saved.append(target_time)
        return True

    monkeypatch.setattr(
        "pacemaker_registry.main.load_race_result", fake_load_race_result
    )
    monkeypatch.setattr(
        "pacemaker_registry.main.save_race_result", fake_save_race_result
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/results",
            data={"result_url": result.source_url, "target_time": "01:55"},
        )

    assert response.status_code == 200
    assert saved == ["01:55"]
    assert "Результат сохранён в реестр" in response.text
    assert "Жигалов Сергей" not in response.text
    assert 'value="01:55"' not in response.text
    assert 'value=""' in response.text


@pytest.mark.anyio
async def test_duplicate_result_is_not_added(monkeypatch) -> None:
    result = RaceResult(
        athlete_name="Жигалов Сергей",
        source_event_id="event-id",
        source_participant_id="827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
        event_name="Забег",
        distance_km="5",
        chip_time="00:29:53",
        target_time="00:30",
        pace="05:59 /км",
        checkpoints=(),
        source_url=(
            "https://results.russiarunning.com/participant/event/race/"
            "827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
        ),
    )

    async def fake_load_race_result(_: str) -> RaceResult:
        return result

    monkeypatch.setattr(
        "pacemaker_registry.main.load_race_result", fake_load_race_result
    )
    monkeypatch.setattr(
        "pacemaker_registry.main.save_race_result", lambda *_: False
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/results",
            data={"result_url": result.source_url, "target_time": "00:30"},
        )

    assert response.status_code == 200
    assert "дубликат не добавлен" in response.text


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
