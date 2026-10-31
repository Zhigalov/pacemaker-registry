"""Public profile and editing pages, ready for a future role model."""
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg import Error as PsycopgError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from pacemaker_registry import avatars, db
from pacemaker_registry.profiles import (
    ACHIEVEMENTS, CONTACT_TYPES, contact_view, normalize_contacts, profile_form_values,
)


def _load(pacemaker_id, include_splits=True, *, with_results=True):
    try:
        profile = db.get_pacemaker_profile(pacemaker_id, include_splits, with_results=with_results)
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
        updated = db.save_pacemaker_profile(profile.id, values['profile_version'], values['motto'],
                                            values['contacts'], values['achievements'], key)
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


def create_profile_router(templates):
    router = APIRouter()

    def edit_response(request, profile, *, values=None, error=None, status_code=200):
        return templates.TemplateResponse(request=request, name='pacemaker_edit.html',
            context={'profile': profile, 'avatar_url': avatars.avatar_url(profile.avatar_key),
                     'values': profile_form_values(profile) if values is None else values,
                     'contact_types': CONTACT_TYPES, 'achievement_types': ACHIEVEMENTS, 'error': error},
            status_code=status_code)

    @router.get('/pacemakers/{pacemaker_id}')
    def profile_page(request: Request, pacemaker_id: int, include_splits: bool = True, saved: bool = False):
        profile = _load(pacemaker_id, include_splits)
        return templates.TemplateResponse(request=request, name='pacemaker.html', context={
            'profile': profile, 'avatar_url': avatars.avatar_url(profile.avatar_key),
            'contacts': [contact_view(c) for c in profile.contacts], 'achievement_types': ACHIEVEMENTS,
            'include_splits': include_splits, 'saved': saved,
        })

    @router.get('/pacemakers/{pacemaker_id}/edit')
    def edit_page(request: Request, pacemaker_id: int):
        return edit_response(request, _load(pacemaker_id, with_results=False))

    @router.post('/pacemakers/{pacemaker_id}/edit')
    async def save_page(request: Request, pacemaker_id: int):
        # Editing is public by design; reject cross-origin form submissions.
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, 'Откройте форму редактирования на сайте реестра.')
        try:
            if int(request.headers.get('content-length', '0')) > avatars.MAX_UPLOAD_BYTES + 100_000:
                raise HTTPException(413, 'Выберите фотографию размером до 5 МБ.')
        except ValueError:
            raise HTTPException(400, 'Некорректный размер запроса.') from None
        profile = await run_in_threadpool(_load, pacemaker_id, with_results=False)
        async with request.form(max_files=1, max_fields=80, max_part_size=4096) as form:
            types, labels, contacts = (form.getlist(name) for name in ('contact_type', 'contact_label', 'contact_value'))
            values = {'motto': str(form.get('motto', '')).strip(),
                      'contacts': [{'type': str(kind), 'label': str(label), 'value': str(value)}
                                   for kind, label, value in zip(types, labels, contacts)],
                      'achievements': list(dict.fromkeys(str(c) for c in form.getlist('achievements'))),
                      'profile_version': str(form.get('profile_version', ''))}
            try:
                if len(values['motto']) > 200:
                    raise ValueError('Девиз должен быть не длиннее 200 символов.')
                if not len(types) == len(labels) == len(contacts):
                    raise ValueError('Некорректные поля контактов. Обновите форму.')
                version = int(values['profile_version'])
                if version < 0 or version != profile.version:
                    return edit_response(request, profile, values=values, status_code=409,
                        error='Профиль уже изменили. Обновите страницу перед сохранением.')
                clean = dict(values, profile_version=version, contacts=normalize_contacts(values['contacts']))
                if any(code not in ACHIEVEMENTS for code in values['achievements']):
                    raise ValueError('Неизвестное достижение. Обновите форму.')
                # Preserve achievements introduced by newer versions of the application.
                clean['achievements'] += [c for c in profile.achievements if c not in ACHIEVEMENTS]
                upload = form.get('photo')
                photo = await upload.read(avatars.MAX_UPLOAD_BYTES + 1) if isinstance(upload, UploadFile) else b''
                if len(photo) > avatars.MAX_UPLOAD_BYTES:
                    raise ValueError('Выберите фотографию размером до 5 МБ.')
                if not photo and isinstance(upload, UploadFile) and upload.filename:
                    raise ValueError('Выбранный файл фотографии пуст.')
                if photo and form.get('remove_photo'):
                    raise ValueError('Выберите новое фото или удаление текущего — одно действие.')
                updated = await run_in_threadpool(_save, profile, clean, photo, bool(form.get('remove_photo')))
            except ValueError as error:
                return edit_response(request, profile, values=values, error=str(error), status_code=422)
            except (PsycopgError, RuntimeError):
                return edit_response(request, profile, values=values, status_code=503,
                    error='Не удалось сохранить профиль. Попробуйте ещё раз; фото нужно выбрать заново.')
        if not updated:
            return edit_response(request, profile, values=values, status_code=409,
                error='Профиль уже изменили. Обновите страницу перед сохранением.')
        return RedirectResponse(f'/pacemakers/{pacemaker_id}?saved=true', status_code=303)

    return router
