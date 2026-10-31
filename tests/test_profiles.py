from io import BytesIO
from unittest.mock import Mock

import pytest
import httpx
from httpx import ASGITransport, AsyncClient
from PIL import Image

from pacemaker_registry import avatars, db
from pacemaker_registry.main import app
from pacemaker_registry.models import PacemakerProfile, RegistryCard
from pacemaker_registry.profile_routes import _save
from pacemaker_registry.profiles import normalize_contacts


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def photo_bytes():
    data = BytesIO()
    exif = Image.Exif()
    exif[270] = 'Private metadata'
    Image.new('RGB', (700, 500), '#176f59').save(data, format='JPEG', exif=exif)
    return data.getvalue()


def profile(**changes):
    return PacemakerProfile(7, 'Петров', 'Пётр', **changes)


def test_avatar_is_reencoded_square_without_metadata():
    with Image.open(BytesIO(avatars.prepare_avatar(photo_bytes()))) as image:
        assert image.format == 'WEBP' and image.size == (512, 512)
        assert not image.getexif()


@pytest.mark.parametrize('data', [b'', b'<svg><script>alert(1)</script></svg>', b'x' * (avatars.MAX_UPLOAD_BYTES + 1)])
def test_avatar_rejects_empty_oversized_and_non_image_uploads(data):
    with pytest.raises(ValueError):
        avatars.prepare_avatar(data)


def test_contact_normalization_and_plain_other():
    assert normalize_contacts([
        {'type': 'telegram', 'value': '@pacer_one'},
        {'type': 'phone', 'value': '+7 (999) 123-45-67'},
        {'type': 'whatsapp', 'value': '+7 (999) 123-45-67'},
        {'type': 'other', 'label': 'Команда', 'value': 'Бежим вместе'},
        {'type': 'vk', 'value': ''},
    ]) == [
        {'type': 'telegram', 'value': 'https://t.me/pacer_one'},
        {'type': 'phone', 'value': '+79991234567'},
        {'type': 'whatsapp', 'value': 'https://wa.me/79991234567'},
        {'type': 'other', 'label': 'Команда', 'value': 'Бежим вместе'},
    ]


@pytest.mark.parametrize('kind,value', [('other', 'javascript:alert(1)'), ('vk', 'https://vk.com.evil.example/'),
    ('telegram', 'https://user:password@t.me/pacer_one'), ('phone', '123'), ('other', 'https://example.org/\nscript')])
def test_unsafe_contacts_are_rejected(kind, value):
    with pytest.raises(ValueError):
        normalize_contacts([{'type': kind, 'value': value}])


@pytest.mark.anyio
async def test_profile_cards_contacts_achievements_and_no_empty_motto(monkeypatch):
    card = RegistryCard(11, 1, 'Забег', '10', '00:50', '49:55', 10, 9, 9.6)
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile(
        contacts=({'type': 'telegram', 'value': 'https://t.me/pacer_one'},),
        achievements=('pshp_graduate',), results=(card, card)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/pacemakers/7?include_splits=false')
    assert response.status_code == 200
    assert response.text.count('class="result-summary"') == 2
    assert 'class="profile-motto"' not in response.text
    assert 'href="https://t.me/pacer_one"' in response.text
    assert 'Выпускник Первой школы пейсмейкеров' in response.text
    assert 'не подтвержден' not in response.text
    assert 'href="/pacemakers/7/edit"' in response.text


@pytest.mark.anyio
async def test_edit_ignores_tampered_name_and_saves_only_profile(monkeypatch):
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile())
    save = Mock(return_value=True)
    monkeypatch.setattr(db, 'save_pacemaker_profile', save)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/pacemakers/7/edit', data={'profile_version': '0',
            'first_name': 'Fake', 'motto': 'Ведём вместе', 'contact_type': 'telegram',
            'contact_value': '@pacer_one', 'contact_label': '', 'achievements': 'pshp_graduate'})
        edit = await client.get('/pacemakers/7/edit')
    assert response.status_code == 303
    save.assert_called_once_with(7, 0, 'Ведём вместе',
        [{'type': 'telegram', 'value': 'https://t.me/pacer_one'}], ['pshp_graduate'], None)
    assert 'name="first_name"' not in edit.text and 'name="last_name"' not in edit.text


@pytest.mark.anyio
async def test_stale_edit_invalid_photo_and_cross_origin_never_save(monkeypatch):
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile(version=1))
    save = Mock()
    monkeypatch.setattr(db, 'save_pacemaker_profile', save)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        stale = await client.post('/pacemakers/7/edit', data={'profile_version': '0'})
        invalid = await client.post('/pacemakers/7/edit', data={'profile_version': '1'},
                                    files={'photo': ('fake.png', b'not-image', 'image/png')})
        foreign = await client.post('/pacemakers/7/edit', headers={'Origin': 'https://evil.example'})
    assert (stale.status_code, invalid.status_code, foreign.status_code) == (409, 422, 403)
    save.assert_not_called()


@pytest.mark.parametrize('success', [True, False])
def test_photo_cleanup_after_save_or_conflict(monkeypatch, success):
    order = []
    monkeypatch.setattr(avatars, 'upload_avatar', lambda *a: order.append('upload') or 'new-key')
    monkeypatch.setattr(db, 'save_pacemaker_profile', lambda *a: order.append('save') or success)
    monkeypatch.setattr(avatars, 'delete_avatar', lambda key: order.append('delete:' + key))
    values = dict(profile_version=0, motto='', contacts=[], achievements=[])
    assert _save(profile(avatar_key='old-key'), values, b'photo', False) == success
    assert order == ['upload', 'save', 'delete:old-key' if success else 'delete:new-key']


def test_failed_database_save_removes_new_upload_only(monkeypatch):
    monkeypatch.setattr(avatars, 'upload_avatar', lambda *a: 'new-key')
    delete = Mock()
    monkeypatch.setattr(avatars, 'delete_avatar', delete)
    def fail(*args):
        raise RuntimeError('Database unavailable')
    monkeypatch.setattr(db, 'save_pacemaker_profile', fail)
    with pytest.raises(RuntimeError):
        _save(profile(avatar_key='old-key'), dict(profile_version=0, motto='', contacts=[], achievements=[]), b'image', False)
    delete.assert_called_once_with('new-key')


def test_storage_uses_metadata_token_and_public_url_has_no_secret(monkeypatch):
    monkeypatch.setenv('AVATAR_BUCKET', 'test-avatars')
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'access_token': 'test-only-token'})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(avatars.httpx, 'Client', lambda **kwargs: client)
    key = 'avatars/7/' + 'a' * 32 + '.webp'
    avatars._request('PUT', key, b'processed-image')
    assert requests[0].headers['Metadata-Flavor'] == 'Google'
    assert requests[1].headers['Authorization'] == 'Bearer test-only-token'
    assert str(requests[1].url) == avatars.avatar_url(key)
    assert 'token' not in avatars.avatar_url(key)


@pytest.mark.anyio
async def test_missing_profile_is_404(monkeypatch):
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/pacemakers/999')).status_code == 404
        assert (await client.get('/pacemakers/999/edit')).status_code == 404
