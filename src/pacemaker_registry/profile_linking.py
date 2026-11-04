"""Choose or create a pacemaker profile for an authenticated account."""
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg import Error as PsycopgError
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from pacemaker_registry import auth, db
from pacemaker_registry.profiles import normalize_profile_login


def _name_key(name):
    return ' '.join(name.casefold().replace('ё', 'е').split())


def _matching_profiles(connection, last_name, first_name):
    last_key, first_key = _name_key(last_name), _name_key(first_name)
    return [
        (pid, f'{last} {first}', account_id)
        for pid, last, first, account_id in connection.execute(
            'SELECT id, last_name, first_name, account_id FROM pacemakers ORDER BY id'
        )
        if _name_key(last) == last_key and _name_key(first) == first_key
    ]


def _linked_id(connection, account_id):
    row = connection.execute('SELECT id FROM pacemakers WHERE account_id = %s', (account_id,)).fetchone()
    return row[0] if row else None


def _bind_profile(account, last_name, first_name, candidate_id, login, publish_email, use_photo):
    with db.connect_database() as connection:
        connection.execute('SELECT id FROM accounts WHERE id = %s FOR UPDATE', (account.id,))
        if linked_id := _linked_id(connection, account.id):
            return linked_id
        # Serialize the name check and insert with result imports and other profile bindings.
        connection.execute('LOCK TABLE pacemakers IN SHARE ROW EXCLUSIVE MODE')
        matches = _matching_profiles(connection, last_name, first_name)
        if candidate_id is not None:
            if not any(pid == candidate_id and owner is None for pid, _, owner in matches):
                raise ValueError('Карточка больше недоступна. Проверьте список.')
            connection.execute('UPDATE pacemakers SET account_id = %s WHERE id = %s', (account.id, candidate_id))
            return candidate_id
        if matches:
            raise ValueError('Профиль с таким именем уже есть. Проверьте список.')
        contacts = [{'type': 'other', 'label': 'Электронная почта', 'value': account.email}] if publish_email and account.email else []
        avatar_key = f'yandex/{account.avatar_id}' if use_photo and account.avatar_url else None
        return connection.execute(
            '''INSERT INTO pacemakers (last_name, first_name, account_id, login, contacts, avatar_key)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id''',
            (last_name, first_name, account.id, login, Jsonb(contacts), avatar_key),
        ).fetchone()[0]


def _suggested_login(login):
    try:
        return normalize_profile_login(login or '') or ''
    except ValueError:
        # Dots are allowed in Yandex logins and can be represented as hyphens here.
        try:
            return normalize_profile_login((login or '').replace('.', '-')) or ''
        except ValueError:
            return ''


def _redirect(path):
    return RedirectResponse(path, status_code=303, headers={'Cache-Control': 'no-store'})


def create_profile_link_router(templates):
    router = APIRouter()

    def render(request, account, *, last_name='', first_name='', searched=False, login=None,
               error=None, status_code=200, publish_email=False, use_photo=True):
        try:
            with db.connect_database() as connection:
                if linked_id := _linked_id(connection, account.id):
                    return _redirect(f'/pacemakers/{linked_id}')
                matches = _matching_profiles(connection, last_name, first_name) if searched else []
        except (PsycopgError, RuntimeError):
            raise HTTPException(503, 'Не удалось загрузить профили. Попробуйте позже.') from None
        return templates.TemplateResponse(request=request, name='profile_link.html', context={
            'last_name': last_name, 'first_name': first_name, 'searched': searched,
            'candidates': [(pid, name) for pid, name, owner in matches if owner is None],
            'has_matches': bool(matches), 'login': _suggested_login(account.login) if login is None else login,
            'error': error, 'publish_email': publish_email, 'use_photo': use_photo,
        }, status_code=status_code)

    @router.get('/account/profile')
    def profile_setup(request: Request):
        if account := getattr(request.state, 'account', None):
            return render(request, account, last_name=account.last_name or '', first_name=account.first_name or '')
        return _redirect('/auth/yandex' if auth.oauth_settings() else '/')

    @router.post('/account/profile')
    def profile_submit(
        request: Request, action: Annotated[str, Form(max_length=10)],
        last_name: Annotated[str, Form(max_length=120)], first_name: Annotated[str, Form(max_length=120)],
        candidate_id: Annotated[int | None, Form(gt=0)] = None,
        login: Annotated[str, Form(max_length=120)] = '',
        publish_email: Annotated[bool, Form()] = False,
        use_photo: Annotated[bool, Form()] = False,
    ):
        account = getattr(request.state, 'account', None)
        settings = auth.oauth_settings()
        if not account or not settings or request.headers.get('origin') != settings[1]:
            raise HTTPException(403, 'Войдите в реестр и откройте форму ещё раз.')
        last_name, first_name = ' '.join(last_name.split()), ' '.join(first_name.split())
        if not all(any(c.isalpha() for c in value) and all(c.isprintable() for c in value)
                   for value in (last_name, first_name)):
            return render(request, account, last_name=last_name, first_name=first_name,
                          error='Укажите фамилию и имя.', status_code=422)
        values = {'last_name': last_name, 'first_name': first_name, 'searched': True}
        if action == 'search':
            return render(request, account, **values)
        if action not in ('link', 'create') or (action == 'link' and candidate_id is None):
            raise HTTPException(400, 'Выберите карточку или создайте профиль.')
        values.update(login=login, publish_email=publish_email, use_photo=use_photo)
        try:
            pid = _bind_profile(account, last_name, first_name, candidate_id if action == 'link' else None,
                                normalize_profile_login(login) if action == 'create' else None, publish_email, use_photo)
        except UniqueViolation as error:
            if error.diag.constraint_name != 'pacemakers_login_key':
                raise
            return render(request, account, **values, error='Этот логин уже занят. Выберите другой.', status_code=422)
        except ValueError as error:
            return render(request, account, **values, error=str(error), status_code=422)
        except (PsycopgError, RuntimeError):
            raise HTTPException(503, 'Не удалось сохранить профиль. Попробуйте позже.') from None
        return _redirect(f'/pacemakers/{pid}')

    return router
