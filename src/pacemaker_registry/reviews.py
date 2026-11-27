"""Runner reviews for a pacemaker, optionally tied to a competition."""

from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg import Error as PsycopgError
from psycopg.rows import dict_row

from pacemaker_registry import auth, db
from pacemaker_registry.avatars import yandex_avatar_url
from pacemaker_registry.filters import Filters

router = APIRouter()
GUEST_COOKIE = '__Host-pacers-review'
GUEST_MAX_AGE = 365 * 24 * 60 * 60
MAX_DB_ID = 2**63 - 1


def list_reviews(pacemaker_id: int) -> list[dict]:
    with db.connect_database() as connection, connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute("""
            SELECT r.event_id, e.name AS event_name, r.score, r.comment, r.account_id,
                   a.yandex_first_name, a.yandex_last_name, a.yandex_avatar_id
            FROM pacemaker_reviews r
            LEFT JOIN events e ON e.id = r.event_id
            LEFT JOIN accounts a ON a.id = r.account_id AND r.comment IS NOT NULL
            WHERE r.pacemaker_id = %s
            ORDER BY r.updated_at DESC, r.event_id, r.author_key
        """, (pacemaker_id,)).fetchall()
    for row in rows:
        first_name = row.pop('yandex_first_name')
        last_name = row.pop('yandex_last_name')
        avatar_id = row.pop('yandex_avatar_id')
        if row['comment']:
            names = [name for name in (first_name, last_name) if name]
            row['author_name'] = ' '.join(names) or 'Пользователь Яндекса'
            row['author_initials'] = ''.join(name[0] for name in names).upper() or 'Я'
            row['author_avatar'] = yandex_avatar_url(avatar_id)
    return rows


def _save_review(pacemaker_id, event_id, token, account_id, score, comment):
    guest = 'guest:' + sha256(token.encode('ascii')).hexdigest()
    author_key = f'account:{account_id}' if account_id is not None else guest
    with db.connect_database() as connection:
        row = connection.execute(f"""
            INSERT INTO pacemaker_reviews (pacemaker_id, event_id, author_key, account_id, score, comment)
            SELECT %s, %s, %s, %s, %s, %s
            FROM pacemakers p
            WHERE p.id = %s AND {db.PUBLIC_PACEMAKER} AND (%s::bigint IS NULL OR EXISTS (
                SELECT 1 FROM race_results rr JOIN events e ON e.id = rr.event_id
                WHERE rr.pacemaker_id = p.id AND rr.event_id = %s AND e.status = 'available'
            ))
            ON CONFLICT (pacemaker_id, event_id, author_key) DO UPDATE
            SET score = EXCLUDED.score,
                comment = COALESCE(EXCLUDED.comment, pacemaker_reviews.comment),
                updated_at = now()
            RETURNING pacemaker_id
        """, (pacemaker_id, event_id, author_key, account_id, score, comment,
              pacemaker_id, event_id, event_id)).fetchone()
        if row is None:
            return False
        if account_id is not None:
            connection.execute("""
                DELETE FROM pacemaker_reviews
                WHERE pacemaker_id = %s AND event_id IS NOT DISTINCT FROM %s AND author_key = %s
            """, (pacemaker_id, event_id, guest))
    return True


@router.post('/pacemakers/{pacemaker_id}/reviews')
def save_review(
    request: Request, pacemaker_id: int, filters: Filters,
    score: Annotated[int, Form(ge=1, le=5)],
    event_id: Annotated[int, Form(ge=0, le=MAX_DB_ID)] = 0,
    comment: Annotated[str, Form(max_length=1000)] = '',
):
    if not 0 < pacemaker_id <= MAX_DB_ID:
        raise HTTPException(404, 'Пейсмейкер не найден.')
    settings = auth.oauth_settings()
    origin = settings[1] if settings else str(request.base_url).rstrip('/')
    if request.headers.get('origin') != origin:
        raise HTTPException(403, 'Откройте форму на сайте реестра.')
    account = auth.request_account(request)
    comment = comment.strip()
    if comment:
        auth.require_account(request)
    token = request.cookies.get(GUEST_COOKIE, '')
    new_token = not auth.TOKEN_PATTERN.fullmatch(token)
    if new_token:
        token = token_urlsafe(32)
    try:
        saved = _save_review(pacemaker_id, event_id or None, token, account.id if account else None, score, comment or None)
    except (PsycopgError, RuntimeError):
        raise HTTPException(503, 'Не удалось сохранить отзыв. Попробуйте позже.') from None
    if not saved:
        message = 'У пейсмейкера нет выступления на этом соревновании.' if event_id else 'Пейсмейкер не найден.'
        raise HTTPException(404, message)
    location = filters.formula_url(f'/pacemakers/{pacemaker_id}', review_saved='true')
    response = RedirectResponse(location + '#reviews', status_code=303, headers={'Cache-Control': 'no-store'})
    if new_token and account is None:
        response.set_cookie(GUEST_COOKIE, token, max_age=GUEST_MAX_AGE, **auth.COOKIE_OPTIONS)
    return response


def _update_comment(pacemaker_id, event_id, author_id, comment, filters):
    if not 0 < pacemaker_id <= MAX_DB_ID:
        raise HTTPException(404, 'Пейсмейкер не найден.')
    try:
        with db.connect_database() as connection:
            updated = connection.execute("""
                UPDATE pacemaker_reviews SET comment = %s
                WHERE pacemaker_id = %s AND event_id IS NOT DISTINCT FROM %s
                    AND author_key = %s AND comment IS NOT NULL
            """, (comment, pacemaker_id, event_id or None, f'account:{author_id}')).rowcount
    except (PsycopgError, RuntimeError):
        raise HTTPException(503, 'Не удалось обновить комментарий. Попробуйте позже.') from None
    if not updated:
        raise HTTPException(404, 'Комментарий не найден.')
    notice = 'comment_removed' if comment is None else 'comment_edited'
    location = filters.formula_url(f'/pacemakers/{pacemaker_id}', **{notice: 'true'})
    return RedirectResponse(location + '#reviews', status_code=303, headers={'Cache-Control': 'no-store'})


@router.post('/pacemakers/{pacemaker_id}/reviews/edit-comment')
def edit_review_comment(
    request: Request, pacemaker_id: int, filters: Filters,
    author_id: Annotated[int, Form(gt=0, le=MAX_DB_ID)],
    event_id: Annotated[int, Form(ge=0, le=MAX_DB_ID)],
    comment: Annotated[str, Form(min_length=1, max_length=1000)],
):
    if auth.require_write(request).id != author_id:
        raise HTTPException(403, 'Редактировать комментарий может только его автор.')
    comment = comment.strip()
    if not comment:
        raise HTTPException(422, 'Введите текст комментария.')
    return _update_comment(pacemaker_id, event_id, author_id, comment, filters)


@router.post('/pacemakers/{pacemaker_id}/reviews/remove-comment')
def remove_review_comment(
    request: Request, pacemaker_id: int, filters: Filters,
    author_id: Annotated[int, Form(gt=0, le=MAX_DB_ID)],
    event_id: Annotated[int, Form(ge=0, le=MAX_DB_ID)],
):
    account = auth.require_write(request)
    if account.id != author_id and not account.is_admin:
        raise HTTPException(403, 'Удалять комментарий может его автор или администратор.')
    return _update_comment(pacemaker_id, event_id, author_id, None, filters)
