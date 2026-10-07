import pytest
from fastapi.testclient import TestClient

from pacemaker_registry import main
from pacemaker_registry.russiarunning import RaceResult, RussiaRunningError


@pytest.mark.parametrize("counter", ["", "0", "-1", "1.5", "1<script>", "1e5", "١٢٣", "9" * 16])
def test_invalid_counter_disables_analytics(monkeypatch, counter):
    monkeypatch.setenv("YANDEX_METRICA_ID", counter)
    response = TestClient(main.app).get("/add")
    assert response.status_code == 200
    assert "/static/analytics.js" not in response.text


def test_configured_counter_and_all_page_templates(monkeypatch):
    monkeypatch.setenv("YANDEX_METRICA_ID", "113525433")
    response = TestClient(main.app).get("/add")
    assert response.text.count('/static/analytics.js?v=1') == 1
    assert 'data-metrica-id="113525433"' in response.text
    for name in ["index", "events", "event", "result", "add"]:
        text = (main.PACKAGE_DIR / "templates" / f"{name}.html").read_text()
        assert text.count("include '_analytics.html'") == 1


@pytest.mark.parametrize("created", [True, False])
def test_save_goal_only_for_new_successful_result(monkeypatch, created):
    sample = RaceResult(
        source_url="https://results.runc.run/event/race/result/123/",
        source_event_id="race", source_participant_id="123", athlete_name="Тест Участник",
        event_name="Тест", distance_km="10", chip_time="49:00", pace="04:54", checkpoints=(), target_time="00:49",
    )
    async def load(_): return sample
    monkeypatch.setattr(main, "load_race_result", load)
    monkeypatch.setattr(main, "save_race_result", lambda *_: created)
    client = TestClient(main.app)
    response = client.post("/results", data={"result_url": sample.source_url, "target_time": "00:49"})
    assert response.status_code == 200
    assert ('data-analytics-goal="result_saved"' in response.text) is created
    assert 'data-analytics-goal="result_preview"' not in response.text
    response = client.post("/results", data={"result_url": sample.source_url, "target_time": "nope"})
    assert 'data-analytics-goal=' not in response.text


def test_parse_error_is_not_a_success_goal(monkeypatch):
    async def fail(_): raise RussiaRunningError("Источник недоступен")
    monkeypatch.setattr(main, "load_race_result", fail)
    response = TestClient(main.app).post("/add", data={"result_url": "https://example.com"})
    assert response.status_code == 502
    assert 'data-analytics-goal=' not in response.text
