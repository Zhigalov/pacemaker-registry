import pytest
from httpx import ASGITransport, AsyncClient

from pacemaker_registry.db import (
    CustomRatingConfig,
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
        lambda event_id=None, rating_mode="automatic", custom_rating=None, include_splits=True: registry,
    )

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
    assert ">Финиш<" in response.text
    assert ">Темп<" in response.text
    assert ">Итог<" in response.text
    assert "60% финиш + 40% темп" in response.text
    assert "Международный Когалымский полумарафон</strong>" in response.text
    assert 'name="include_splits"' in response.text
    assert '<option value="true" selected>' in response.text
    assert "−7 с" in response.text
    assert "Как считается рейтинг" in response.text
    assert "Автопилот" in response.text
    assert "Строгий финиш" in response.text
    assert "Зеркальный допуск" in response.text
    assert "Конструктор" in response.text
    assert "Формула рейтинга" not in response.text
    assert "rating-chart-line" in response.text
    assert 'data-rating-chart data-rating-mode="strict"' in response.text
    assert 'data-rating-chart data-rating-mode="symmetric"' in response.text
    assert "±45 секунд" in response.text
    assert 'aria-label="Показать, как считается рейтинг"' in response.text
    assert 'aria-controls="rating-methodology"' in response.text
    assert 'name="custom_points" value="7,8,9,10,9,8,7"' in response.text
    assert "data-custom-editor" in response.text
    assert "Детали результата" in response.text
    assert "Здесь появится список выступлений" not in response.text
    assert "Все соревнования" in response.text
    assert "Московский марафон" in response.text
    assert '<option value="0" selected>' in response.text
    assert 'name="rating_mode"' in response.text
    assert '<option value="automatic" selected>' in response.text
    assert '<noscript><button class="button registry-filter-submit"' in response.text


@pytest.mark.anyio
async def test_home_page_passes_selected_event_to_registry(monkeypatch) -> None:
    requested_filters: list[tuple[int | None, str, CustomRatingConfig, bool]] = []
    registry = Registry(
        pacemakers=(),
        event_count=1,
        result_count=0,
        events=(
            RegistryEvent(id=1, name="Когалымский полумарафон"),
            RegistryEvent(id=2, name="Московский марафон"),
        ),
        selected_event_id=2,
    )

    def fake_load_registry(
        event_id: int | None = None,
        rating_mode: str = "automatic",
        custom_rating: CustomRatingConfig = CustomRatingConfig(),
        include_splits: bool = True,
    ) -> Registry:
        requested_filters.append((event_id, rating_mode, custom_rating, include_splits))
        return Registry(
            pacemakers=registry.pacemakers,
            event_count=registry.event_count,
            result_count=registry.result_count,
            events=registry.events,
            selected_event_id=registry.selected_event_id,
            rating_mode=rating_mode,
            custom_rating=custom_rating,
            include_splits=include_splits,
        )

    monkeypatch.setattr(
        "pacemaker_registry.main.load_registry", fake_load_registry
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/?event_id=2&rating_mode=symmetric&include_splits=false"
        )
        await client.get("/?event_id=1&rating_mode=strict")
        await client.get("/?event_id=0")
        custom_response = await client.get(
            "/?rating_mode=custom&custom_points=6,7,8,10,8,7,6"
            "&custom_left_start=15&custom_right_start=55"
            "&custom_left_decay=20&custom_right_decay=40"
        )

    assert response.status_code == 200
    assert [(event_id, mode) for event_id, mode, _, _ in requested_filters] == [
        (2, "symmetric"),
        (1, "strict"),
        (None, "automatic"),
        (None, "custom"),
    ]
    assert '<option value="2" selected>' in response.text
    assert '<option value="symmetric" selected>' in response.text
    assert "Показать все" in response.text
    assert 'href="/?rating_mode=symmetric&include_splits=false"' in response.text
    assert requested_filters[0][3] is False
    assert '<option value="false" selected>' in response.text
    assert requested_filters[-1][2] == CustomRatingConfig(
        points=(6, 7, 8, 10, 8, 7, 6),
        left_exponent_start=15,
        right_exponent_start=55,
        left_decay=20,
        right_decay=40,
    )
    assert '<option value="custom" selected>' in custom_response.text
    assert 'name="custom_left_start" value="15"' in custom_response.text
    assert 'name="custom_right_start" value="55"' in custom_response.text


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
