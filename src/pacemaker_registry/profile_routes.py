"""Public profiles and authenticated editing."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg import Error as PsycopgError
from psycopg.errors import UniqueViolation
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from pacemaker_registry import auth, avatars, db
from pacemaker_registry.profiles import (
    ACHIEVEMENTS, CONTACT_TYPES, RECORD_DISTANCES, contact_view,
    ProfileEdit, read_profile_form, profile_form_values,
)


def _load(pacemaker_id, *, with_results=True):
    try:
        profile = db.get_pacemaker_profile(pacemaker_id, with_results=with_results)
    except (PsycopgError, RuntimeError):
        raise HTTPException(503, 'Не удалось загрузить пейсмейкера. Попробуйте позже.') from None
    if profile is None:
        raise HTTPException(404, 'Пейсмейкер не найден.')
    return profile


def _save(profile, values, photo, remove_photo):
    new_key = None
    key = None if remove_photo else profile.avatar_key
    try:
        if photo:
            new_key = avatars.upload_avatar(profile.id, photo)
            key = new_key
        updated = db.save_pacemaker_profile(
            profile.id, values.profile_version, values.about, values.contacts, values.achievements, key,
            login=values.login, city=values.city, club=values.club, personal_records=values.personal_records,
        )
    except Exception:
        if new_key:
            avatars.delete_avatar(new_key)
        raise
    if not updated:
        if new_key:
            avatars.delete_avatar(new_key)
        return False
    if profile.avatar_key and profile.avatar_key != key:
        avatars.delete_avatar(profile.avatar_key)
    return True


def _require_profile_editor(account, pacemaker_id):
    if not account.can_edit_profile(pacemaker_id):
        raise HTTPException(403, 'Редактировать профиль может его владелец или администратор.')


def create_profile_router(templates):
    router = APIRouter()

    def edit_response(request, profile, *, values=None, error=None, status_code=200):
        return templates.TemplateResponse(request=request, name='pacemaker_edit.html',
            context={'profile': profile, 'avatar_url': avatars.avatar_url(profile.avatar_key),
                     'values': profile_form_values(profile) if values is None else values,
                     'contact_types': CONTACT_TYPES,
                     'achievement_types': ACHIEVEMENTS, 'record_distances': RECORD_DISTANCES,
                     'can_edit_achievements': auth.request_account(request).is_admin,
                     'choices': db.get_profile_choices(), 'error': error},
            status_code=status_code)

    @router.get('/p/{login}')
    def public_profile(request: Request, login: str, saved: bool = False):
        try:
            pacemaker_id = db.find_pacemaker_login(login)
        except (PsycopgError, RuntimeError):
            raise HTTPException(503, 'Не удалось загрузить профиль. Попробуйте позже.') from None
        if pacemaker_id is None:
            raise HTTPException(404, 'Пейсмейкер не найден.')
        return profile_page(request, pacemaker_id, saved)

    @router.get('/pacemakers/{pacemaker_id}')
    def profile_page(request: Request, pacemaker_id: int, saved: bool = False):
        profile = _load(pacemaker_id)
        account = auth.request_account(request)
        return templates.TemplateResponse(request=request, name='pacemaker.html', context={
            'profile': profile, 'avatar_url': avatars.avatar_url(profile.avatar_key),
            'contacts': [contact_view(c) for c in profile.contacts], 'achievement_types': ACHIEVEMENTS,
            'saved': saved, 'record_distances': RECORD_DISTANCES,
            'can_edit_profile': account is not None and account.can_edit_profile(pacemaker_id),
        })

    @router.get('/pacemakers/{pacemaker_id}/edit')
    def edit_page(request: Request, pacemaker_id: int):
        _require_profile_editor(auth.require_account(request), pacemaker_id)
        return edit_response(request, _load(pacemaker_id, with_results=False))

    @router.post('/pacemakers/{pacemaker_id}/edit')
    async def save_page(request: Request, pacemaker_id: int):
        account = auth.require_write(request)
        _require_profile_editor(account, pacemaker_id)
        try:
            if int(request.headers.get('content-length', '0')) > avatars.MAX_UPLOAD_BYTES + 100_000:
                raise HTTPException(413, 'Выберите фотографию размером до 2 МБ.')
        except ValueError:
            raise HTTPException(400, 'Некорректный размер запроса.') from None
        profile = await run_in_threadpool(_load, pacemaker_id, with_results=False)
        async with request.form(max_files=1, max_fields=160, max_part_size=64_000) as form:
            values, complete_contacts = read_profile_form(form, profile, is_admin=account.is_admin)
            try:
                if not complete_contacts:
                    raise ValueError('Некорректные поля контактов. Обновите форму.')
                version = int(values['profile_version'])
                if version < 0 or version != profile.version:
                    return edit_response(request, profile, values=values, status_code=409,
                        error='Профиль уже изменили. Обновите страницу перед сохранением.')
                clean = ProfileEdit.validate(values, profile, is_admin=account.is_admin)
                upload = form.get('photo')
                photo = await upload.read(avatars.MAX_UPLOAD_BYTES + 1) if isinstance(upload, UploadFile) else b''
                if len(photo) > avatars.MAX_UPLOAD_BYTES:
                    raise ValueError('Выберите фотографию размером до 2 МБ.')
                if not photo and isinstance(upload, UploadFile) and upload.filename:
                    raise ValueError('Выбранный файл фотографии пуст.')
                if photo and form.get('remove_photo'):
                    raise ValueError('Выберите новое фото или удаление текущего — одно действие.')
                updated = await run_in_threadpool(_save, profile, clean, photo, bool(form.get('remove_photo')))
            except UniqueViolation as error:
                if error.diag.constraint_name != 'pacemakers_login_key':
                    raise
                return edit_response(request, profile, values=values, status_code=422,
                    error='Этот логин уже занят. Выберите другой.')
            except ValueError as error:
                return edit_response(request, profile, values=values, error=str(error), status_code=422)
            except (PsycopgError, RuntimeError):
                return edit_response(request, profile, values=values, status_code=503,
                    error='Не удалось сохранить профиль. Попробуйте ещё раз; фото нужно выбрать заново.')
        if not updated:
            return edit_response(request, profile, values=values, status_code=409,
                error='Профиль уже изменили. Обновите страницу перед сохранением.')
        return RedirectResponse((f'/p/{clean.login}' if clean.login else f'/pacemakers/{pacemaker_id}') + '?saved=true', status_code=303)

    return router
