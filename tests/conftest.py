import pytest

from pacemaker_registry import auth


@pytest.fixture
def admin_session(monkeypatch):
    """Supply an administrator for tests focused on existing forms and saving."""
    monkeypatch.setattr(auth, "request_account", lambda request: auth.SignedInAccount(1, "admin", None, is_admin=True))
    monkeypatch.setattr(auth, "oauth_settings", lambda: ("test-client", "http://test"))
    check_origin = auth.check_origin

    def verify_explicit_origin(request):
        if "origin" in request.headers:
            check_origin(request)

    monkeypatch.setattr(auth, "check_origin", verify_explicit_origin)
