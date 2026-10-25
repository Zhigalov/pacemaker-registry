"""Editing stays public until the planned role model is introduced."""
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from pacemaker_registry import main
from pacemaker_registry.rating import EventRatingConfig, PaceRatingConfig


@pytest.mark.parametrize('path,payload,save_name', [
    ('/events/1/rating', asdict(EventRatingConfig()), 'save_event_rating'),
    ('/events/1/pace-rating', asdict(PaceRatingConfig()), 'save_event_pace_rating'),
    ('/results/1/target-time', {'target_time': '01:39', 'previous_target_time': '01:40'},
     'update_result_target_time'),
])
def test_editing_does_not_require_login(monkeypatch, path, payload, save_name):
    monkeypatch.setattr(main, '_load_event_or_404', lambda *_: SimpleNamespace())
    monkeypatch.setattr(main, '_load_result_or_404', lambda *_: SimpleNamespace())
    save = Mock(return_value=True)
    monkeypatch.setattr(main, save_name, save)
    with TestClient(main.app) as client:
        response = client.post(path, data=payload, follow_redirects=False)
    assert response.status_code == 303
    assert 'www-authenticate' not in response.headers
    save.assert_called_once()
