import pytest


@pytest.fixture(autouse=True)
def isolated_editor_credentials(monkeypatch):
    # Synthetic test credentials only; never inherit real admin settings.
    monkeypatch.setenv('ADMIN_USERNAME', 'test-admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-only-password')
