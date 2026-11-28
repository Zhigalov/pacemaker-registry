"""Public feedback and a private support inbox."""
from io import BytesIO
import os
import re
from threading import Semaphore
from typing import Annotated

import httpx
from fastapi import APIRouter, Form, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg import Error as PsycopgError
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from pacemaker_registry import auth, db
from pacemaker_registry.profiles import normalize_social

MAX_FILES = 5
MAX_UPLOAD = 350 * 1024
MAX_STORED_IMAGE = 420 * 1024
MAX_TEXT = 4000
PAGE_SIZE = 25
PRIVATE = {'Cache-Control': 'private, no-store'}
RequestId = Annotated[int, Path(ge=1, le=2**63 - 1)]
_decode_slot = Semaphore(1)


def _contact(kind, value):
    value = value.strip()
    if not 1 <= len(value) <= 254 or not value.isprintable():
        raise ValueError('Укажите контакт для ответа.')
    if kind == 'telegram':
        return normalize_social('telegram', value)
    if kind != 'email' or not re.fullmatch(r'[^\s@<>]+@[^\s@<>.]+(?:\.[^\s@<>.]+)+', value):
        raise ValueError('Укажите email в формате name@example.com.')
    return value


def _prepare_image(data):
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError('Не удалось загрузить изображение. Выберите файл меньшего размера.')
    try:
        with _decode_slot, Image.open(BytesIO(data), formats=['JPEG', 'PNG', 'WEBP']) as original:
            if original.width * original.height > 8_000_000:
                raise ValueError('Уменьшите изображение до 8 мегапикселей.')
            original.thumbnail((1600, 1600))
            image = ImageOps.exif_transpose(original).convert('RGB')
            for quality in (86, 68):
                output = BytesIO()
                image.save(output, format='WEBP', quality=quality)
                if output.tell() <= MAX_STORED_IMAGE:
                    return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('Выберите изображение в формате JPEG, PNG или WebP.') from None
    raise ValueError('Не удалось уменьшить изображение. Выберите файл меньшего размера.')


def _save_request(body, kind, contact, images):
    with db.connect_database() as conn:
        request_id, = conn.execute(
            'INSERT INTO support_requests (body, contact_kind, contact) VALUES (%s,%s,%s) RETURNING id',
            (body, kind, contact),
        ).fetchone()
        with conn.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO support_images (request_id, position, image) VALUES (%s,%s,%s)',
                [(request_id, position, image) for position, image in enumerate(images, 1)],
            )
    return request_id


def _telegram_settings():
    token = os.getenv('SUPPORT_TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('SUPPORT_TELEGRAM_CHAT_ID', '')
    if re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) and re.fullmatch(r'-[1-9][0-9]*', chat_id):
        return token, chat_id
    return None


def _notify(request_id):
    settings = _telegram_settings()
    if settings is None:
        return False
    token, chat_id = settings
    url = f'https://pacersreg.ru/admin/support/{request_id}'
    try:
        with db.connect_database() as conn:
            pending = conn.execute('SELECT telegram_sent_at FROM support_requests WHERE id = %s FOR UPDATE',
                                   (request_id,)).fetchone()
            if pending is None or pending[0] is not None:
                return pending is not None
            response = httpx.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={
                    'chat_id': chat_id,
                    'text': f'Новое обращение №{request_id}',
                    'reply_markup': {'inline_keyboard': [[{'text': 'Открыть в реестре', 'url': url}]]},
                },
                timeout=3,
            )
            if not response.is_success or response.json().get('ok') is not True:
                return False
            conn.execute('UPDATE support_requests SET telegram_sent_at = now() WHERE id = %s', (request_id,))
    except (httpx.HTTPError, ValueError, AttributeError, PsycopgError, RuntimeError):
        # The inquiry is already committed. HTTP exceptions may contain the bot token.
        return False
    return True


def _require_admin(account):
    if not account.is_admin:
        raise HTTPException(403, 'Обращения доступны администраторам.')


def _close_request(request_id, account_id, resolution):
    with db.connect_database() as conn:
        return conn.execute(
            """UPDATE support_requests r
               SET closed_at = now(), closed_by = a.id,
                   closed_by_login = COALESCE(NULLIF(a.yandex_login, ''), 'Аккаунт №' || a.id::text),
                   resolution = %s
               FROM accounts a
               WHERE r.id = %s AND r.closed_at IS NULL AND a.id = %s AND a.is_admin
               RETURNING r.id""", (resolution, request_id, account_id),
        ).fetchone() is not None


def create_support_router(templates):
    router = APIRouter()

    def form_response(request, *, receipt=None, body='', contact_kind='telegram', contact='', error=None, status_code=200):
        return templates.TemplateResponse(
            request=request, name='support.html', status_code=status_code, headers=PRIVATE,
            context={'receipt': receipt, 'body': body, 'contact_kind': contact_kind, 'contact': contact,
                     'error': error, 'max_files': MAX_FILES, 'max_text': MAX_TEXT, 'max_upload': MAX_UPLOAD},
        )

    @router.get('/support')
    def support_form(request: Request, sent: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None):
        return form_response(request, receipt=sent)

    @router.post('/support')
    async def submit_support(request: Request):
        settings = auth.oauth_settings()
        origin = settings[1] if settings else str(request.base_url).rstrip('/')
        if request.headers.get('origin') != origin:
            raise HTTPException(403, 'Откройте форму на сайте реестра.')
        body, kind, contact = '', 'telegram', ''
        code = 422
        try:
            if int(request.headers.get('content-length', '0')) > 2 * 1024 * 1024:
                raise ValueError('Вложения слишком большие. Откройте форму в браузере со сжатием изображений.')
            async with request.form(max_files=MAX_FILES, max_fields=3, max_part_size=MAX_TEXT * 4) as form:
                body = str(form.get('body', '')).strip()
                kind = str(form.get('contact_kind', ''))
                contact = str(form.get('contact', '')).strip()
                if not 1 <= len(body) <= MAX_TEXT or '\x00' in body:
                    raise ValueError(f'Опишите вопрос или проблему. Максимум {MAX_TEXT} символов.')
                normalized = _contact(kind, contact)
                uploads = form.getlist('images')
                if not all(isinstance(file, UploadFile) for file in uploads):
                    raise ValueError('Выберите до пяти изображений.')
                data = [await file.read(MAX_UPLOAD + 1) for file in uploads if file.filename]
            images = await run_in_threadpool(lambda: [_prepare_image(file) for file in data])
            request_id = await run_in_threadpool(_save_request, body, kind, normalized, images)
        except StarletteHTTPException as failure:
            if failure.status_code != 400:
                raise
            error = 'Прикрепите до пяти изображений и проверьте длину сообщения.'
        except ValueError as failure:
            error = str(failure)
        except (PsycopgError, RuntimeError):
            code, error = 503, 'Не удалось отправить обращение. Попробуйте ещё раз.'
        else:
            await run_in_threadpool(_notify, request_id)
            return RedirectResponse(f'/support?sent={request_id}', status_code=303, headers=PRIVATE)
        return form_response(request, body=body[:MAX_TEXT], contact_kind=kind, contact=contact[:254],
                             error=error, status_code=code)

    @router.get('/admin/support')
    def inbox(request: Request, page: Annotated[int, Query(ge=1, le=100_000)] = 1):
        _require_admin(auth.require_account(request))
        with db.connect_database() as conn, conn.cursor(row_factory=dict_row) as cursor:
            entries = cursor.execute(
                """SELECT id, left(body, 180) AS excerpt, created_at, closed_at, closed_by_login
                   FROM support_requests ORDER BY (closed_at IS NOT NULL), id DESC LIMIT %s OFFSET %s""",
                (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
            ).fetchall()
        return templates.TemplateResponse(request=request, name='support_inbox.html', headers=PRIVATE,
            context={'entries': entries[:PAGE_SIZE], 'page': page, 'has_next': len(entries) > PAGE_SIZE})

    @router.get('/admin/support/{request_id}')
    def detail(request: Request, request_id: RequestId, delivery: Annotated[str | None, Query()] = None):
        _require_admin(auth.require_account(request))
        with db.connect_database() as conn, conn.cursor(row_factory=dict_row) as cursor:
            entry = cursor.execute(
                """SELECT r.*, ARRAY(SELECT position FROM support_images WHERE request_id = r.id ORDER BY position) AS images
                   FROM support_requests r WHERE id = %s""", (request_id,),
            ).fetchone()
        if entry is None:
            raise HTTPException(404, 'Обращение не найдено.')
        return templates.TemplateResponse(request=request, name='support_request.html', headers=PRIVATE,
            context={'entry': entry, 'delivery': delivery, 'notifications_enabled': _telegram_settings() is not None})

    @router.get('/admin/support/{request_id}/images/{position}')
    def attachment(request: Request, request_id: RequestId, position: Annotated[int, Path(ge=1, le=MAX_FILES)]):
        _require_admin(auth.require_account(request))
        with db.connect_database() as conn:
            row = conn.execute('SELECT image FROM support_images WHERE request_id = %s AND position = %s',
                               (request_id, position)).fetchone()
        if row is None:
            raise HTTPException(404, 'Изображение не найдено.')
        return Response(row[0], media_type='image/webp', headers={**PRIVATE, 'X-Content-Type-Options': 'nosniff'})

    @router.post('/admin/support/{request_id}/close')
    def close(request: Request, request_id: RequestId, resolution: Annotated[str, Form(max_length=2000)]):
        account = auth.require_write(request)
        _require_admin(account)
        resolution = resolution.strip()
        if not resolution or '\x00' in resolution:
            raise HTTPException(422, 'Укажите резолюцию.')
        if not _close_request(request_id, account.id, resolution):
            raise HTTPException(409, 'Обращение уже закрыто или недоступно.')
        return RedirectResponse(f'/admin/support/{request_id}', status_code=303, headers=PRIVATE)

    @router.post('/admin/support/{request_id}/notify')
    def retry(request: Request, request_id: RequestId):
        _require_admin(auth.require_write(request))
        delivery = 'sent' if _notify(request_id) else 'failed'
        return RedirectResponse(f'/admin/support/{request_id}?delivery={delivery}', status_code=303, headers=PRIVATE)

    return router
