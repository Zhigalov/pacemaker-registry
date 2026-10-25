"""Small temporary editor gate, not a replacement for the planned role model."""
import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials


basic = HTTPBasic(auto_error=False, realm='Registry editor')


def require_editor(request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]):
    username, password = os.getenv('ADMIN_USERNAME', ''), os.getenv('ADMIN_PASSWORD', '')
    if not username or not password:
        raise HTTPException(503, 'Редактирование временно недоступно: не настроен доступ администратора.')
    if credentials is None:
        raise HTTPException(401, 'Нужен вход администратора.', headers={'WWW-Authenticate': 'Basic realm="Registry editor"'})
    user_matches = secrets.compare_digest(credentials.username.encode(), username.encode())
    password_matches = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (user_matches & password_matches):
        raise HTTPException(401, 'Неверный логин или пароль.', headers={'WWW-Authenticate': 'Basic realm="Registry editor"'})
    # Browsers automatically reuse Basic credentials: reject cross-site form submissions.
    # TLS terminates at the existing proxy; the internal connection may be HTTP.
    origin = request.headers.get('origin')
    same_site_origins = {f'https://{request.url.netloc}', f'{request.url.scheme}://{request.url.netloc}'}
    if ((origin is not None and origin not in same_site_origins)
            or request.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(403, 'Откройте форму на этом сайте и повторите сохранение.')
