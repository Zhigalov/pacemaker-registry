"""Profile values and contact links; imported names are never edited here."""
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


CONTACT_TYPES = {
    'telegram': ('Telegram', 'username'),
    'instagram': ('Instagram', 'username'),
    'whatsapp': ('WhatsApp', '+7 999 123-45-67'),
    'max': ('MAX', 'username или u/код профиля'),
    'phone': ('Телефон', '+7 999 123-45-67'),
    'vk': ('ВКонтакте', 'username или id123456'),
    'facebook': ('Facebook', 'username'),
    'other': ('Другое', 'Ссылка или текст'),
}
ACHIEVEMENTS = {'pshp_graduate': {
    'title': 'Выпускник Первой школы пейсмейкеров',
    'image': '/static/first-pacers-school.jpg',
}}
CONTACT_PREFIXES = {
    'telegram': 'https://t.me/', 'instagram': 'https://instagram.com/',
    'max': 'https://max.ru/', 'vk': 'https://vk.com/', 'facebook': 'https://facebook.com/',
}
SERVICE_HOSTS = {
    'telegram': ('t.me', 'telegram.me'),
    'instagram': ('instagram.com',),
    'max': ('max.ru',),
    'vk': ('vk.com', 'vk.ru'),
    'facebook': ('facebook.com', 'fb.com'),
    'whatsapp': ('wa.me', 'api.whatsapp.com'),
}


def _web_url(value: str) -> str:
    if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('Проверьте ссылку: в ней есть пробелы или недопустимые символы.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('Проверьте формат ссылки.') from None
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname
            or parsed.username or parsed.password or port not in {None, 80, 443}):
        raise ValueError('Укажите полную ссылку, начинающуюся с https:// или http://.')
    return urlunsplit(parsed)


def normalize_contacts(rows: list[dict]) -> list[dict[str, str]]:
    if len(rows) > 20:
        raise ValueError('Можно добавить не больше 20 контактов.')
    contacts = []
    for row in rows:
        kind, value = str(row.get('type', '')), str(row.get('value', '')).strip()
        label = str(row.get('label', '')).strip()
        if not value:
            continue
        if kind not in CONTACT_TYPES or len(value) > 500 or len(label) > 60:
            raise ValueError('Проверьте тип и длину контакта (до 500 символов, подпись — до 60).')
        if kind in {'phone', 'whatsapp'}:
            number = re.sub(r'[\s()\-]', '', value)
            if not re.fullmatch(r'\+[1-9][0-9]{6,14}', number):
                raise ValueError('Укажите телефон с кодом страны, например +7 999 123-45-67.')
            value = number if kind == 'phone' else 'https://wa.me/' + number[1:]
        elif kind == 'other':
            if value.startswith(('https://', 'http://')):
                value = _web_url(value)
            elif re.match(r'(?i)^[a-z][a-z0-9+.-]*:', value):
                raise ValueError('Для ссылки «Другое» используйте http:// или https://.')
        else:
            nickname = value.removeprefix('@')
            pattern = {'telegram': r'[A-Za-z0-9_]{5,32}', 'instagram': r'[A-Za-z0-9_.]{1,30}',
                       'max': r'(?:u/)?[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}'}.get(
                           kind, r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}')
            if not re.fullmatch(pattern, nickname):
                raise ValueError(f'{CONTACT_TYPES[kind][0]}: укажите ник без ссылки и пробелов, например username.')
            value = CONTACT_PREFIXES[kind] + nickname
        contact = {'type': kind, 'value': value}
        if kind == 'other' and label:
            contact['label'] = label
        if contact not in contacts:
            contacts.append(contact)
    return contacts


def contact_view(contact: dict) -> dict:
    """Render only validated schemes, including for manually imported DB values."""
    kind, value = contact['type'], contact['value']
    href = None
    if kind == 'phone' and re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
        href = 'tel:' + value
    elif value.startswith(('https://', 'http://')):
        try:
            href = _web_url(value)
        except ValueError:
            pass
    return {'label': contact.get('label') or CONTACT_TYPES.get(kind, ('Другое', ''))[0],
            'value': value, 'href': href}


def profile_form_values(profile) -> dict:
    contacts = list(profile.contacts)
    rows = []
    for kind, (label, placeholder) in CONTACT_TYPES.items():
        if kind == 'other':
            continue
        matches = [c for c in contacts if c['type'] == kind]
        rows.extend([dict(c, value=contact_input(c)) for c in matches] or [{'type': kind, 'value': ''}])
    rows.extend(c for c in contacts if c['type'] == 'other')
    return {'motto': profile.motto or '', 'contacts': rows,
            'achievements': list(profile.achievements), 'profile_version': profile.version}


def contact_input(contact: dict) -> str:
    """Existing canonical URLs become editable handles; no data migration needed."""
    kind, value = contact['type'], contact['value']
    if kind not in SERVICE_HOSTS or not value.startswith(('https://', 'http://')):
        return value
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.hostname.removeprefix('www.').lower() not in SERVICE_HOSTS[kind]:
        return value
    path = parsed.path.strip('/')
    if kind == 'whatsapp':
        number = parse_qs(parsed.query).get('phone', [path])[0]
        return '+' + number.lstrip('+')
    if kind == 'facebook' and path == 'profile.php':
        return parse_qs(parsed.query).get('id', [value])[0]
    return path
