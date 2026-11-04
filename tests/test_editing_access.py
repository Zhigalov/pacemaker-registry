from dataclasses import asdict
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from pacemaker_registry import auth, main
from pacemaker_registry.models import RegistryEvent
from pacemaker_registry.rating import EventRatingConfig, PaceRatingConfig


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_ID", "test-client")
    with TestClient(main.app, base_url="https://pacersreg.ru", follow_redirects=False) as client:
        yield client


@pytest.mark.parametrize("path,payload,save_name", [
    ("/events/1/rating", asdict(EventRatingConfig()), "save_event_rating"),
    ("/events/1/pace-rating", asdict(PaceRatingConfig()), "save_event_pace_rating"),
])
@pytest.mark.parametrize("account,status", [
    (None, 401),
    (auth.SignedInAccount(2, "pacer", None), 403),
    (auth.SignedInAccount(1, "captain", None), 303),
    (auth.SignedInAccount(2, "admin", None, is_admin=True), 303),
])
def test_event_edit_requires_its_captain_or_admin(client, monkeypatch, path, payload, save_name, account, status):
    monkeypatch.setattr(auth, "request_account", lambda request: account)
    monkeypatch.setattr(main, "_load_event_or_404", lambda *_: RegistryEvent(1, "Забег", captain_account_id=1))
    save = Mock(return_value=True)
    monkeypatch.setattr(main, save_name, save)
    response = client.post(path, data=payload, headers={"Origin": "https://pacersreg.ru"})
    assert response.status_code == status
    assert save.call_count == (1 if status == 303 else 0)


def test_protected_actions_reject_guests_and_foreign_origins(client, monkeypatch):
    assert client.post("/results", data={"result_url": "https://example.org", "target_time": "01:45"}).status_code == 401
    assert client.post("/results/1/target-time", data={"target_time": "01:39", "previous_target_time": "01:40"}).status_code == 401
    assert client.get("/pacemakers/1/edit").status_code == 401
    assert client.get("/admin/roles").status_code == 401
    monkeypatch.setattr(auth, "request_account", lambda request: auth.SignedInAccount(2, "pacer", None, pacemaker_id=2))
    assert client.get("/pacemakers/1/edit").status_code == 403
    assert client.post("/pacemakers/1/edit", headers={"Origin": "https://pacersreg.ru"}).status_code == 403
    assert client.get("/admin/roles").status_code == 403
    assert client.post("/admin/roles", data={"account_id": 2, "make_admin": "true"},
                       headers={"Origin": "https://pacersreg.ru"}).status_code == 403
    assert client.post("/results", data={"result_url": "https://example.org", "target_time": "01:45"},
                       headers={"Origin": "https://elsewhere.example"}).status_code == 403
