from base64 import urlsafe_b64encode
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from pacemaker_registry import auth
from pacemaker_registry.db import Registry
from pacemaker_registry.main import app


ORIGIN = "https://pacersreg.ru"
CLIENT_ID = "example-client"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("YANDEX_OAUTH_ORIGIN", ORIGIN)
    monkeypatch.setattr("pacemaker_registry.main.load_registry", lambda *args, **kwargs: Registry((), 0, 0))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client


def start_flow(client):
    response = client.get("/auth/yandex")
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    cookie_header = response.headers["set-cookie"]
    assert all(flag in cookie_header for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
    redirect = urlsplit(response.headers["location"])
    assert (redirect.scheme, redirect.netloc, redirect.path) == ("https", "oauth.yandex.ru", "/authorize")
    query = parse_qs(redirect.query)
    state, verifier = client.cookies[auth.FLOW_COOKIE].split(".")
    assert query == {
        "client_id": [CLIENT_ID],
        "redirect_uri": [ORIGIN + auth.CALLBACK_PATH],
        "response_type": ["code"],
        "scope": ["login:info"],
        "optional_scope": ["login:avatar"],
        "state": [state],
        "code_challenge": [urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")],
        "code_challenge_method": ["S256"],
    }
    return state, verifier


def test_login_logout_and_cross_origin_rejection(client, monkeypatch):
    state, verifier = start_flow(client)
    upstream_client = httpx.Client

    def provider(request):
        if request.url.path == "/token":
            assert request.url.host == "oauth.yandex.ru"
            assert parse_qs(request.content.decode()) == {
                "client_id": [CLIENT_ID], "code": ["sample-code"],
                "code_verifier": [verifier], "grant_type": ["authorization_code"],
            }
            return httpx.Response(200, json={"access_token": "upstream-token"})
        assert request.url.host == "login.yandex.ru"
        assert request.headers["authorization"] == "OAuth upstream-token"
        return httpx.Response(200, json={
            "id": "12345", "client_id": CLIENT_ID, "login": "sample.login",
            "default_avatar_id": "123/avatar-test", "is_avatar_empty": False,
            "last_name": "  Семёнов ", "first_name": "Артём",
        })

    monkeypatch.setattr(auth.httpx, "Client", lambda **kwargs: upstream_client(
        transport=httpx.MockTransport(provider), **kwargs,
    ))
    token = "t" * 43
    sessions = {}

    def create_session(identity, previous_hash):
        assert identity == auth.YandexIdentity("12345", "sample.login", "123/avatar-test", None, "Семёнов", "Артём")
        assert previous_hash is None
        sessions[token] = auth.SignedInAccount(10, identity.login, identity.avatar_id)
        return token

    monkeypatch.setattr(auth, "create_session", create_session)
    monkeypatch.setattr(auth, "current_account", lambda request: sessions.get(request.cookies.get(auth.SESSION_COOKIE)))
    monkeypatch.setattr(auth, "end_session", lambda request: sessions.pop(request.cookies[auth.SESSION_COOKIE]))

    assert 'href="/auth/yandex"' in client.get("/").text
    result = client.get(auth.CALLBACK_PATH, params={"state": state, "code": "sample-code"})
    assert result.status_code == 303
    assert result.headers["location"] == "/account/profile"
    assert "upstream-token" not in str(result.headers)
    assert client.cookies.get(auth.FLOW_COOKIE) is None
    assert client.cookies[auth.SESSION_COOKIE] == token
    assert "Max-Age=604800" in result.headers["set-cookie"]
    logged_in = client.get("/")
    assert 'aria-expanded="false" aria-controls="site-account-panel"' in logged_in.text
    assert 'id="site-account-panel" hidden' in logged_in.text
    assert "<strong>sample.login</strong>" in logged_in.text
    assert "https://avatars.yandex.net/get-yapic/123/avatar-test/islands-retina-middle" in logged_in.text
    assert 'action="/auth/logout"' in logged_in.text
    assert 'href="/auth/yandex"' not in logged_in.text
    assert logged_in.headers["cache-control"] == "private, no-store"
    assert "Cookie" in logged_in.headers["vary"]
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout", headers={"Origin": "https://elsewhere.test"}).status_code == 403
    assert token in sessions
    assert client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 303
    assert client.cookies.get(auth.SESSION_COOKIE) is None
    assert not sessions
    assert 'href="/auth/yandex"' in client.get("/").text


@pytest.mark.parametrize(("query", "message"), [
    ({"state": "x" * 43, "code": "unexpected"}, "expired"),
    ({"error": "access_denied"}, "cancelled"),
    ({"error": "unauthorized_client"}, "unavailable"),
    ({}, "unavailable"),
    ({"code": "rejected"}, "unavailable"),
])
def test_callback_errors(client, monkeypatch, query, message):
    state, _ = start_flow(client)

    def reject(client_id, code, verifier):
        assert code == "rejected"
        raise httpx.ConnectError("test failure")

    monkeypatch.setattr(auth, "yandex_identity", reject)
    result = client.get(auth.CALLBACK_PATH, params={"state": state, **query})
    assert result.headers["location"] == f"/?auth_error={message}"
    assert client.cookies.get(auth.FLOW_COOKIE) is None
    assert client.cookies.get(auth.SESSION_COOKIE) is None
    assert auth.AUTH_MESSAGES[message] in client.get(result.headers["location"]).text


def test_login_is_hidden_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("YANDEX_OAUTH_CLIENT_ID")
    assert 'href="/auth/yandex"' not in client.get("/").text
    assert client.get("/auth/yandex").status_code == 404


def test_account_without_photo_has_initial(client, monkeypatch):
    client.cookies.set(auth.SESSION_COOKIE, "t" * 43)
    monkeypatch.setattr(auth, "current_account", lambda request: auth.SignedInAccount(10, "sample.login", None))
    page = client.get("/").text
    assert '<span class="site-account-portrait" aria-hidden="true">' in page
    assert "\n          S\n" in page
    assert "sample.login" in page
    assert "avatars.yandex.net" not in page


def test_optional_email_scope_is_requested_only_when_enabled(client, monkeypatch):
    monkeypatch.setenv("YANDEX_OAUTH_EMAIL_ENABLED", "1")
    location = client.get("/auth/yandex").headers["location"]
    assert parse_qs(urlsplit(location).query)["optional_scope"] == ["login:avatar login:email"]
