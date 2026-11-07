from io import BytesIO
from unittest.mock import Mock

import pytest
import httpx
from httpx import ASGITransport, AsyncClient
from PIL import Image

from pacemaker_registry import avatars, db
from pacemaker_registry.main import app
from pacemaker_registry.models import PacemakerProfile, RegistryCard, Registry, RegistryPacemaker
from pacemaker_registry.profile_routes import _save
from pacemaker_registry.profiles import normalize_contacts, profile_form_values, contact_view

pytestmark = pytest.mark.usefixtures("admin_session")


@pytest.fixture(autouse=True)
def choices(monkeypatch):
    monkeypatch.setattr(db, 'get_profile_choices', lambda: {'cities': ['Москва'], 'clubs': []})


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


@pytest.mark.parametrize('kind,nick,url', [
    ('telegram', '@pacer_one', 'https://t.me/pacer_one'),
    ('instagram', 'pacer.one', 'https://instagram.com/pacer.one'),
    ('vk', 'id123456', 'https://vk.com/id123456'),
    ('facebook', '@pacer.one', 'https://facebook.com/pacer.one'),
    ('max', 'pacer_one', 'https://max.ru/pacer_one'),
    ('max', 'u/profile_code', 'https://max.ru/u/profile_code'),
])
def test_social_handles_build_canonical_urls_and_prefill_as_handles(kind, nick, url):
    contacts = normalize_contacts([{'type': kind, 'value': nick}])
    assert contacts == [{'type': kind, 'value': url}]
    values = profile_form_values(profile(contacts=tuple(contacts)))
    row = next(c for c in values['contacts'] if c['type'] == kind)
    assert row['value'] == nick.removeprefix('@')
    assert normalize_contacts(values['contacts']) == contacts


@pytest.mark.parametrize('kind,value', [
    ('telegram', 'https://t.me/+invite'), ('instagram', 'instagram.com/pacer/post'),
    ('vk', 'https://vk.com/'), ('facebook', 'pacer?redirect=bad'),
    ('max', 'https://max.ru/u/pacer/extra'), ('whatsapp', 'https://wa.me/message/invite'),
    ('telegram', '@@pacer_one'), ('instagram', 'name with spaces'),
    ('facebook', 'https://facebook.com/profile.php'),
    ('facebook', 'https://facebook.com/profile.php?id=123&id=456'),
    ('whatsapp', 'https://api.whatsapp.com/send'),
    ('telegram', 'https://t.me.evil.example/pacer_one'),
    ('instagram', 'https://evil.example/pacer'),
    ('max', 'javascript:alert(1)'), ('telegram', 'https://t.me/pacer\n_one'),
])
def test_social_inputs_reject_invalid_or_wrong_service_links(kind, value):
    with pytest.raises(ValueError):
        normalize_contacts([{'type': kind, 'value': value}])


@pytest.mark.parametrize('kind,value,expected', [
    ('telegram', ' https://t.me/pacer_one ', 'https://t.me/pacer_one'),
    ('telegram', 't.me/pacer_one/', 'https://t.me/pacer_one'),
    ('telegram', 'https://telegram.me/pacer_one?utm_source=app', 'https://t.me/pacer_one'),
    ('instagram', 'www.instagram.com/pacer.one/?igsh=tracking', 'https://instagram.com/pacer.one'),
    ('vk', 'https://vk.ru/id123?from=profile', 'https://vk.com/id123'),
    ('vk', 'VK.COM/pacer_one/', 'https://vk.com/pacer_one'),
    ('facebook', 'https://www.facebook.com/pacer.one/', 'https://facebook.com/pacer.one'),
    ('facebook', 'https://facebook.com/profile.php?id=123456&tracking=yes', 'https://www.facebook.com/profile.php?id=123456'),
    ('max', 'max.ru/u/profile_code', 'https://max.ru/u/profile_code'),
    ('whatsapp', 'https://wa.me/79991234567', 'https://wa.me/79991234567'),
    ('whatsapp', 'wa.me/79991234567?text=Hello', 'https://wa.me/79991234567'),
    ('whatsapp', 'https://api.whatsapp.com/send?phone=79991234567&text=Hello', 'https://wa.me/79991234567'),
    ('strava', 'strava.com/athletes/123456', 'https://www.strava.com/athletes/123456'),
])
def test_social_links_and_handles_roundtrip(kind, value, expected):
    clean = normalize_contacts([{'type': kind, 'value': value}])
    assert clean == [{'type': kind, 'value': expected}]
    assert normalize_contacts(profile_form_values(profile(contacts=tuple(clean)))['contacts']) == clean


def test_link_and_handle_for_same_profile_are_deduplicated():
    assert normalize_contacts([
        {'type': 'telegram', 'value': '@pacer_one'},
        {'type': 'telegram', 'value': 'https://t.me/pacer_one?source=app'},
    ]) == [{'type': 'telegram', 'value': 'https://t.me/pacer_one'}]


def test_existing_whatsapp_and_facebook_links_remain_editable_without_migration():
    values = profile_form_values(profile(contacts=(
        {'type': 'whatsapp', 'value': 'https://wa.me/79991234567'},
        {'type': 'facebook', 'value': 'https://www.facebook.com/profile.php?id=123456'},
    )))
    nonempty = [c for c in values['contacts'] if c['value']]
    assert nonempty == [{'type': 'whatsapp', 'value': '+79991234567'},
                        {'type': 'facebook', 'value': 'https://www.facebook.com/profile.php?id=123456'}]
    assert normalize_contacts(values['contacts']) == [
        {'type': 'whatsapp', 'value': 'https://wa.me/79991234567'},
        {'type': 'facebook', 'value': 'https://www.facebook.com/profile.php?id=123456'},
    ]


@pytest.mark.parametrize('kind,value,display', [
    ('telegram', 'https://t.me/pacer_one', '@pacer_one'),
    ('instagram', 'https://instagram.com/pacer.one', '@pacer.one'),
    ('vk', 'https://vk.com/id123', '@id123'),
    ('facebook', 'https://facebook.com/pacer.one', '@pacer.one'),
    ('facebook', 'https://www.facebook.com/profile.php?id=123456', '@123456'),
    ('max', 'https://max.ru/pacer_one', '@pacer_one'),
    ('whatsapp', 'https://wa.me/79991234567', '+79991234567'),
    ('phone', '+79991234567', '+79991234567'),
])
def test_contact_rows_show_handle_or_phone_and_matching_brand(kind, value, display):
    view = contact_view({'type': kind, 'value': value})
    assert view['display_value'] == display
    assert view['icon'] == kind
    assert view['href'] == ('tel:' + value if kind == 'phone' else value)


def test_other_telegram_link_keeps_custom_label_and_gets_telegram_icon():
    view = contact_view({'type': 'other', 'label': 'Глазами пейсмейкера', 'value': 'https://t.me/pacer_eyes'})
    assert view['icon'] == 'telegram' and view['display_value'] == '@pacer_eyes'
    assert view['label'] == 'Глазами пейсмейкера'
    assert contact_view({'type': 'other', 'value': 'Без ссылки'})['href'] is None
    assert contact_view({'type': 'other', 'value': 'javascript:alert(1)'})['href'] is None


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
    assert 'src="/static/social-telegram.svg"' in response.text
    assert '<strong>@pacer_one</strong>' in response.text
    assert 'Выпускник Первой школы пейсмейкеров' in response.text
    assert 'src="/static/first-pacers-school.jpg"' in response.text
    assert 'class="achievement-tooltip"' in response.text
    assert 'не подтвержден' not in response.text
    assert 'href="/pacemakers/7/edit"' in response.text


@pytest.mark.anyio
@pytest.mark.parametrize('contact_value', ['@pacer_one', 'https://t.me/pacer_one?source=app'])
async def test_edit_ignores_tampered_name_and_saves_only_profile(monkeypatch, contact_value):
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile())
    save = Mock(return_value=True)
    monkeypatch.setattr(db, 'save_pacemaker_profile', save)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/pacemakers/7/edit', data={'profile_version': '0',
            'first_name': 'Fake', 'motto': 'Ведём вместе', 'contact_type': 'telegram',
            'contact_value': contact_value, 'contact_label': '', 'achievements': 'pshp_graduate'})
        edit = await client.get('/pacemakers/7/edit')
    assert response.status_code == 303
    save.assert_called_once_with(7, 0, 'Ведём вместе',
        [{'type': 'telegram', 'value': 'https://t.me/pacer_one'}], ['pshp_graduate'], None, login=None, city='', club='', personal_records={})
    assert 'name="first_name"' not in edit.text and 'name="last_name"' not in edit.text
    assert 'Выбрать фото</label>' in edit.text
    assert 'Файл не выбран' in edit.text
    assert 'class="visually-hidden" id="profile-photo"' in edit.text
    assert 'Вставьте ссылку на профиль или укажите ник' in edit.text
    assert '>t.me/</span>' not in edit.text


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
    monkeypatch.setattr(db, 'save_pacemaker_profile', lambda *a, **k: order.append('save') or success)
    monkeypatch.setattr(avatars, 'delete_avatar', lambda key: order.append('delete:' + key))
    values = dict(profile_version=0, about='', contacts=[], achievements=[])
    assert _save(profile(avatar_key='old-key'), values, b'photo', False) == success
    assert order == ['upload', 'save', 'delete:old-key' if success else 'delete:new-key']


def test_failed_database_save_removes_new_upload_only(monkeypatch):
    monkeypatch.setattr(avatars, 'upload_avatar', lambda *a: 'new-key')
    delete = Mock()
    monkeypatch.setattr(avatars, 'delete_avatar', delete)
    def fail(*args, **kwargs):
        raise RuntimeError('Database unavailable')
    monkeypatch.setattr(db, 'save_pacemaker_profile', fail)
    with pytest.raises(RuntimeError):
        _save(profile(avatar_key='old-key'), dict(profile_version=0, about='', contacts=[], achievements=[]), b'image', False)
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


@pytest.mark.anyio
@pytest.mark.parametrize('key,has_photo', [('avatars/7/' + 'a' * 32 + '.webp', True),
                                         (None, False), ('https://evil.example/a.jpg', False)])
async def test_registry_uses_saved_avatar_or_initials_and_links_profile(monkeypatch, key, has_photo):
    monkeypatch.setenv('AVATAR_BUCKET', 'test-avatars')
    pacer = RegistryPacemaker(7, 'Петров Пётр', 'ПП', 10, (), avatar_key=key)
    monkeypatch.setattr('pacemaker_registry.main.load_registry', lambda *a, **k: Registry((pacer,), 0, 0))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/')
    assert response.status_code == 200
    assert 'ПП</span>' in response.text and 'href="/pacemakers/7"' in response.text
    assert ('loading="lazy"' in response.text) == has_photo
    if has_photo:
        assert 'https://storage.yandexcloud.net/test-avatars/' + key in response.text


@pytest.mark.parametrize('login', ['aa', '1name', 'name with spaces', '../admin', 'имя', 'a' * 31])
def test_login_rejects_invalid_public_addresses(login):
    from pacemaker_registry.profiles import normalize_profile_details
    values = profile_form_values(profile())
    with pytest.raises(ValueError):
        normalize_profile_details(dict(values, login=login))


def test_extended_profile_validation_and_strava_roundtrip():
    from pacemaker_registry.profiles import normalize_profile_details
    values = profile_form_values(profile())
    values.update(login='My_Login', about='Я' * 5000, city='  Новый   город ', club=' Клуб ',
                  personal_records={'5': {'time': '18:02', 'year': '2025', 'url': 'https://example.org/result'}})
    clean = normalize_profile_details(values)
    assert (clean['login'], clean['city'], clean['club']) == ('my_login', 'Новый город', 'Клуб')
    assert clean['personal_records']['5'] == {'time': '00:18:02', 'year': 2025, 'url': 'https://example.org/result'}
    contacts = normalize_contacts([{'type': 'strava', 'value': '123456'}])
    assert contacts == [{'type': 'strava', 'value': 'https://www.strava.com/athletes/123456'}]
    assert contact_view(contacts[0])['display_value'] == '@123456'
    assert normalize_contacts(profile_form_values(profile(contacts=tuple(contacts)))['contacts']) == contacts


@pytest.mark.parametrize('record', [
    {'time': '0:00', 'year': '2025', 'url': 'https://example.org'},
    {'time': '18:60', 'year': '2025', 'url': 'https://example.org'},
    {'time': '18:02', 'year': '1800', 'url': 'https://example.org'},
    {'time': '18:02', 'year': '2999', 'url': 'https://example.org'},
    {'time': '18:02', 'year': '', 'url': 'https://example.org'},
    {'time': '18:02', 'year': '2025', 'url': 'javascript:alert(1)'},
    {'time': '18:02', 'year': '2025', 'url': ''},
])
def test_incomplete_or_invalid_record_rejected(record):
    from pacemaker_registry.profiles import normalize_profile_details
    with pytest.raises(ValueError):
        normalize_profile_details(dict(profile_form_values(profile()), personal_records={'5': record}))


@pytest.mark.anyio
async def test_public_login_and_details_edit_and_errors_preserve_fields(monkeypatch):
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile(login='pacer'))
    monkeypatch.setattr(db, 'find_pacemaker_login', lambda login: 7 if login == 'pacer' else None)
    save = Mock(return_value=True)
    monkeypatch.setattr(db, 'save_pacemaker_profile', save)
    data = {'profile_version': '0', 'about': 'Я' * 5000, 'login': 'MY_LOGIN',
            'city': ' Новый   город ', 'club': 'Наш клуб', 'record_5_time': '19:01',
            'record_5_year': '2025', 'record_5_url': 'https://example.org/race'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/p/pacer')).status_code == 200
        assert (await client.get('/p/missing')).status_code == 404
        saved = await client.post('/pacemakers/7/edit', data=data)
        assert saved.status_code == 303 and saved.headers['location'] == '/p/my_login?saved=true'
        assert save.call_args.kwargs == {'login': 'my_login', 'city': 'Новый город', 'club': 'Наш клуб',
            'personal_records': {'5': {'time': '00:19:01', 'year': 2025, 'url': 'https://example.org/race'}}}
        invalid = await client.post('/pacemakers/7/edit', data=dict(data, login='no spaces allowed'))
        assert invalid.status_code == 422 and 'Наш клуб' in invalid.text and '19:01' in invalid.text
        assert 'Я' * 5000 in invalid.text
        assert save.call_count == 1


@pytest.mark.anyio
async def test_profile_renders_records_and_about_as_plain_text(monkeypatch):
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile(
        login='pacer', city='Москва', club='Наш клуб', about='<script>alert(1)</script>',
        personal_records={'42.2': {'time': '03:00:00', 'year': 2025, 'url': 'https://example.org/race'}}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/pacemakers/7')
    assert 'pacersreg.ru/p/pacer' in response.text and 'Наш клуб' in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    assert '42,2 км' in response.text and '03:00:00' in response.text


@pytest.mark.anyio
async def test_login_collision_is_explained_without_losing_entered_fields(monkeypatch):
    from psycopg.errors import UniqueViolation
    monkeypatch.setattr(db, 'get_pacemaker_profile', lambda *a, **k: profile())
    class LoginConflict(UniqueViolation):
        @property
        def diag(self):
            return type('Diagnostic', (), {'constraint_name': 'pacemakers_login_key'})()
    monkeypatch.setattr(db, 'save_pacemaker_profile', Mock(side_effect=LoginConflict()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        r = await client.post('/pacemakers/7/edit', data={'profile_version': '0', 'login': 'taken',
            'about': 'Моё описание', 'city': 'Москва', 'club': 'Мой клуб'})
    assert r.status_code == 422 and 'Этот логин уже занят' in r.text
    assert 'Моё описание' in r.text and 'Мой клуб' in r.text and 'value="taken"' in r.text


@pytest.mark.parametrize('value,expected', [
    ('123456', 'https://www.strava.com/athletes/123456'),
    ('https://www.strava.com/athletes/123456?utm_source=app', 'https://www.strava.com/athletes/123456'),
    ('https://strava.com/athletes/pacer_one/', 'https://www.strava.com/athletes/pacer_one'),
    ('https://strava.app.link/E7j9NbZnJ7b', 'https://strava.app.link/E7j9NbZnJ7b'),
    ('Sergey Zhigalov пользуется Strava. https://strava.app.link/E7j9NbZnJ7b', 'https://strava.app.link/E7j9NbZnJ7b'),
])
def test_strava_accepts_shared_text_profile_links_and_id(value, expected):
    clean = normalize_contacts([{'type': 'strava', 'value': value}])
    assert clean == [{'type': 'strava', 'value': expected}]
    form = profile_form_values(profile(contacts=tuple(clean)))
    assert normalize_contacts(form['contacts']) == clean
    view = contact_view(clean[0])
    assert view['href'] == expected and view['icon'] == 'strava'
    if 'app.link' in expected:
        assert view['display_value'] == 'Профиль Strava'


@pytest.mark.parametrize('value', [
    'https://strava.app.link.evil.example/test', 'https://strava.com/activities/123',
    'https://strava.com/clubs/123', 'http://strava.app.link/test',
    'https://user:password@strava.app.link/test',
    'https://strava.app.link/test https://example.org',
    'javascript:alert(1)', 'https://strava.app.link/test/extra',
])
def test_strava_rejects_non_profile_web_urls_and_unsafe_links(value):
    with pytest.raises(ValueError):
        normalize_contacts([{'type': 'strava', 'value': value}])
