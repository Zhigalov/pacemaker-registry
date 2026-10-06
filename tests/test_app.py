import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.db import (
    Registry,
    RegistryEvent,
    RegistryPacemaker,
    RegistryResult,
)
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
                        finish_rating=9.533,
                        splits_rating=8.7,
                        rating=9.2,
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
        events=(
            RegistryEvent(id=1, name="Международный Когалымский полумарафон"),
            RegistryEvent(id=2, name="Московский марафон"),
        ),
    )
    monkeypatch.setattr(
        "pacemaker_registry.main.load_registry",
        lambda event_id=None, include_splits=True, min_events=1: registry,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "Реестр пейсмейкеров" in response.text
    assert 'href="/static/favicon.png?v=equal-flags"' in response.text
    assert 'href="/static/apple-touch-icon.png?v=equal-flags"' in response.text
    assert 'href="/static/styles.css?v=compact-toolbar"' in response.text
    assert 'Результаты забегов' not in response.text
    assert 'class="visually-hidden">Пейсмейкеры</h1>' in response.text
    assert 'class="filter-hint" role="tooltip">За всю историю пейсера</span>' in response.text
    assert response.text.count('--rating-color: hsl(') == 3
    assert 'href="/add"' in response.text
    assert "Жигалов Сергей" in response.text
    assert "Международный Когалымский полумарафон" in response.text
    assert "05:24 /км" in response.text
    assert "05:23 /км" in response.text
    assert 'class="result-event"' not in response.text
    assert "Рейтинг 9,5 из 10" in response.text
    assert ">Цель<" in response.text
    assert ">Факт<" in response.text
    assert ">Финиш<" in response.text
    assert ">Темп<" in response.text
    assert ">Итог<" in response.text
    assert "60% финиш + 40% темп" in response.text
    assert "Отклонение до 5 секунд на километр не штрафуется" in response.text
    assert "После 30 секунд штраф растёт по экспоненте" in response.text
    assert "Международный Когалымский полумарафон</strong>" in response.text
    assert 'name="include_splits"' in response.text
    assert 'type="checkbox"' in response.text
    assert "−7 с" in response.text
    assert "Как считается рейтинг" in response.text
    assert "Автопилот" not in response.text
    assert "Конструктор" not in response.text
    assert 'href="/events"' in response.text
    assert 'class="event-directory"' not in response.text
    assert "Детали результата" in response.text
    assert "Здесь появится список выступлений" not in response.text
    assert "Все соревнования" in response.text
    assert "Московский марафон" in response.text
    assert '<option value="0" selected>' in response.text
    assert 'name="rating_mode"' not in response.text
    assert '<noscript><button class="button registry-filter-submit"' in response.text


@pytest.mark.anyio
async def test_home_page_filters_and_checkbox(monkeypatch) -> None:
    requested = []
    def fake_load_registry(event_id=None, include_splits=True, min_events=1):
        requested.append((event_id, include_splits))
        return Registry((), 1, 0, events=(RegistryEvent(2, "Марафон"),),
                        selected_event_id=event_id, include_splits=include_splits)
    monkeypatch.setattr("pacemaker_registry.main.load_registry", fake_load_registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/?event_id=2&include_splits=false")
        await client.get("/?event_id=1")
        await client.get("/?event_id=0")
        await client.get("/?include_splits=false&include_splits=true")
        # Old shared links no longer select a different formula.
        await client.get("/?rating_mode=custom&custom_points=1,1,1,1,1,1,1")
    assert response.status_code == 200
    assert requested == [(2, False), (1, True), (None, True), (None, True), (None, True)]
    assert 'href="/events/2"' in response.text
    assert 'href="/?include_splits=false&amp;min_events=1"' in response.text


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
    assert 'href="/static/favicon.png?v=equal-flags"' in response.text
    assert 'href="/static/apple-touch-icon.png?v=equal-flags"' in response.text
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
async def test_add_form_parses_result_url_from_query_without_saving(monkeypatch) -> None:
    result_url = (
        "https://results.russiarunning.com/participant/"
        "kazanmarathon2026/21km/edf120ca-d0dd-408e-94d2-52bb06f4f101"
    )
    loaded_urls: list[str] = []

    async def fake_load_race_result(value: str) -> RaceResult:
        loaded_urls.append(value)
        return RaceResult(
            athlete_name="Андреев Артем",
            source_event_id="21ca4584-8f8b-47df-85ee-4fbcef005074",
            source_participant_id="edf120ca-d0dd-408e-94d2-52bb06f4f101",
            event_name="СберПрайм Казанский марафон 2026",
            distance_km="21,1",
            chip_time="01:19:53",
            target_time="01:20",
            pace="03:47 /км",
            checkpoints=(),
            source_url=value,
        )

    def fail_if_saved(*_: object) -> bool:
        raise AssertionError("GET /add must not save a result")

    monkeypatch.setattr(
        "pacemaker_registry.main.load_race_result", fake_load_race_result
    )
    monkeypatch.setattr(
        "pacemaker_registry.main.save_race_result", fail_if_saved
    )
    monkeypatch.setattr(
        "pacemaker_registry.main.get_event_target_time_type", lambda _: None
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/add", params={"result_url": result_url})

    assert response.status_code == 200
    assert loaded_urls == [result_url]
    assert "Андреев Артем" in response.text
    assert "01:19:53" in response.text
    assert 'value="01:20"' in response.text
    assert "Проверьте полученные данные" in response.text
    assert "Сохранить в реестр" in response.text


@pytest.mark.anyio
async def test_add_form_uses_saved_event_target_time_type(monkeypatch) -> None:
    async def fake_load_race_result(value: str) -> RaceResult:
        return RaceResult(
            athlete_name="Пейсер Второй",
            source_event_id="event-id",
            source_participant_id="827f5fcd-eaaf-41c0-93d2-ed4fd58de206",
            event_name="Забег с ровными флагами",
            distance_km="21,1",
            chip_time="01:04:20",
            target_time="01:04",
            pace="03:03 /км",
            checkpoints=(),
            source_url=value,
        )

    monkeypatch.setattr(
        "pacemaker_registry.main.load_race_result", fake_load_race_result
    )
    monkeypatch.setattr(
        "pacemaker_registry.main.get_event_target_time_type", lambda _: "round"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/add",
            params={
                "result_url": (
                    "https://results.russiarunning.com/participant/"
                    "event/race/827f5fcd-eaaf-41c0-93d2-ed4fd58de206"
                )
            },
        )

    assert response.status_code == 200
    assert 'value="01:05"' in response.text
    assert 'value="01:04"' not in response.text


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
