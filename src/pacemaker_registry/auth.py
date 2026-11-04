import base64
import os
import re
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg import Error as PsycopgError

from pacemaker_registry import db


router = APIRouter(prefix="/auth")
CALLBACK_PATH = "/auth/yandex/callback"
FLOW_COOKIE = "__Host-pacers-oauth"
SESSION_COOKIE = "__Host-pacers-session"
FLOW_SECONDS = 600
SESSION_SECONDS = 7 * 24 * 60 * 60
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
COOKIE_OPTIONS = {"secure": True, "httponly": True, "samesite": "lax", "path": "/"}

AUTH_MESSAGES = {
    "cancelled": "Вход через Яндекс отменён.",
    "expired": "Срок ожидания входа истёк. Начните вход заново.",
    "unavailable": "Не удалось войти через Яндекс. Попробуйте ещё раз.",
    "logout": "Не удалось завершить сеанс. Попробуйте выйти ещё раз.",
}


def oauth_settings() -> tuple[str, str] | None:
    client_id = os.getenv("YANDEX_OAUTH_CLIENT_ID", "").strip()
    origin = os.getenv("YANDEX_OAUTH_ORIGIN", "https://pacersreg.ru").rstrip("/")
    parsed = urlsplit(origin)
    if client_id and parsed.scheme == "https" and parsed.hostname and not (
        parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
    ):
        return client_id, origin
    return None


def auth_context(request: Request) -> dict:
    return {
        "yandex_login_enabled": oauth_settings() is not None,
        "signed_in": getattr(request.state, "account_id", None) is not None,
        "auth_message": AUTH_MESSAGES.get(request.query_params.get("auth_error"))
        if request.url.path == "/" else None,
    }


def _session_hash(request: Request) -> bytes | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    return sha256(token.encode("ascii")).digest() if TOKEN_PATTERN.fullmatch(token) else None


def current_account_id(request: Request) -> int | None:
    token_hash = _session_hash(request)
    if token_hash is None:
        return None
    with db.connect_database() as connection:
        row = connection.execute(
            "SELECT account_id FROM account_sessions WHERE token_hash = %s AND expires_at > now()",
            (token_hash,),
        ).fetchone()
    return row[0] if row else None


def create_session(yandex_user_id: str, previous_hash: bytes | None) -> str:
    token = token_urlsafe(32)
    with db.connect_database() as connection:
        account_id, = connection.execute(
            """INSERT INTO accounts (yandex_user_id) VALUES (%s)
               ON CONFLICT (yandex_user_id) DO UPDATE SET yandex_user_id = EXCLUDED.yandex_user_id
               RETURNING id""",
            (yandex_user_id,),
        ).fetchone()
        connection.execute("DELETE FROM account_sessions WHERE expires_at <= now()")
        if previous_hash is not None:
            connection.execute("DELETE FROM account_sessions WHERE token_hash = %s", (previous_hash,))
        connection.execute(
            """INSERT INTO account_sessions (token_hash, account_id, expires_at)
               VALUES (%s, %s, now() + %s * interval '1 second')""",
            (sha256(token.encode("ascii")).digest(), account_id, SESSION_SECONDS),
        )
    return token


def end_session(request: Request) -> None:
    token_hash = _session_hash(request)
    if token_hash is not None:
        with db.connect_database() as connection:
            connection.execute("DELETE FROM account_sessions WHERE token_hash = %s", (token_hash,))


def _redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303, headers={
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    })


@router.get("/yandex")
def login() -> RedirectResponse:
    settings = oauth_settings()
    if settings is None:
        raise HTTPException(404)
    client_id, origin = settings
    state, verifier = token_urlsafe(32), token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": origin + CALLBACK_PATH,
        "scope": "login:info",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    response = _redirect("https://oauth.yandex.ru/authorize?" + query)
    response.set_cookie(FLOW_COOKIE, f"{state}.{verifier}", max_age=FLOW_SECONDS, **COOKIE_OPTIONS)
    return response


def yandex_user_id(client_id: str, code: str, verifier: str) -> str:
    with httpx.Client(timeout=4.0, trust_env=False) as client:
        token = client.post("https://oauth.yandex.ru/token", data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
        })
        token.raise_for_status()
        access_token = token.json()["access_token"]
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Invalid OAuth token")
        info = client.get("https://login.yandex.ru/info", params={"format": "json"},
                          headers={"Authorization": "OAuth " + access_token})
        info.raise_for_status()
        identity = info.json()
    subject = identity["id"]
    if identity["client_id"] != client_id or not isinstance(subject, str) or not re.fullmatch(r"[0-9]{1,64}", subject):
        raise ValueError("Invalid Yandex identity")
    return subject


@router.get("/yandex/callback")
def callback(request: Request) -> RedirectResponse:
    settings = oauth_settings()
    if settings is None:
        raise HTTPException(404)
    client_id, _ = settings
    state, _, verifier = request.cookies.get(FLOW_COOKIE, "").partition(".")
    returned_state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    error = request.query_params.get("error")
    session = None
    if not (TOKEN_PATTERN.fullmatch(state) and TOKEN_PATTERN.fullmatch(verifier)
            and TOKEN_PATTERN.fullmatch(returned_state) and compare_digest(state, returned_state)):
        location = "/?auth_error=expired"
    elif error == "access_denied":
        location = "/?auth_error=cancelled"
    elif error or not code or len(code) > 1024:
        location = "/?auth_error=unavailable"
    else:
        try:
            subject = yandex_user_id(client_id, code, verifier)
            session = create_session(subject, _session_hash(request))
            location = "/"
        except (httpx.HTTPError, ValueError, KeyError, TypeError, PsycopgError, RuntimeError):
            location = "/?auth_error=unavailable"
    response = _redirect(location)
    response.delete_cookie(FLOW_COOKIE, **COOKIE_OPTIONS)
    if session:
        response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_SECONDS, **COOKIE_OPTIONS)
    return response


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    settings = oauth_settings()
    if settings is None:
        raise HTTPException(404)
    if request.headers.get("origin") != settings[1]:
        raise HTTPException(403)
    try:
        end_session(request)
    except (PsycopgError, RuntimeError):
        return _redirect("/?auth_error=logout")
    response = _redirect("/")
    response.delete_cookie(SESSION_COOKIE, **COOKIE_OPTIONS)
    response.delete_cookie(FLOW_COOKIE, **COOKIE_OPTIONS)
    return response
